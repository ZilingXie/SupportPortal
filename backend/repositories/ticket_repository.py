from __future__ import annotations

import copy
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, TypeVar
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Json
try:
    from psycopg_pool import ConnectionPool, PoolTimeout
except ImportError:  # pragma: no cover - exercised in environments without pool support
    ConnectionPool = None
    PoolTimeout = None

LOGGER = logging.getLogger(__name__)

_VALID_STATUSES = {"open", "communicating", "escalated", "investigating", "resolved"}
_VALID_ASSIGNMENT_STATUSES = {"pending", "assigned", "resolved"}
_VALID_DISPATCH_STATUSES = {"pending", "assigned", "failed", "resolved"}
_VALID_WORKSPACE_ROLES = {"admin", "engineer"}
_VALID_ENGINEER_AVAILABILITY = {"available", "unavailable"}
_VALID_ROLES = {"customer", "assistant", "engineer", "system"}
_VALID_INVESTIGATION_ROLES = {"engineer_ai", "engineer", "system"}
_VALID_INVESTIGATION_STATES = {"active", "awaiting_confirmation", "awaiting_final_approval", "closed"}
_VALID_HITL_FEEDBACK_TYPES = {"approve", "revise", "reject", "resolve", "reopen"}
_VALID_HITL_DIAGNOSIS_CORRECTNESS = {"correct", "partially_correct", "incorrect", "not_applicable"}
_VALID_HITL_ROOT_CAUSE_CORRECTNESS = {"confirmed", "likely", "incorrect", "unknown", "not_applicable"}
_VALID_HITL_EVIDENCE_QUALITY = {"sufficient", "partial", "insufficient", "wrong"}
_VALID_HITL_CITATION_QUALITY = {"correct", "partial", "missing", "wrong", "not_applicable"}
_VALID_HITL_CUSTOMER_REPLY_QUALITY = {"sendable", "needs_edit", "unsafe", "not_applicable"}
_VALID_HITL_MEMORY_CANDIDATE = {"yes", "no", "needs_review"}
_VALID_HITL_MEMORY_SAFETY = {"customer_safe", "internal_only", "do_not_store"}
_VALID_CASE_MEMORY_LEDGER_STATUSES = {"ledger_only", "candidate", "active", "rejected", "superseded"}
_VALID_CASE_MEMORY_ACTIVE_STATUSES = {"inactive", "active", "disabled", "superseded"}
_VALID_CASE_MEMORY_QUALITY_LABELS = {
    "candidate",
    "ledger_only",
    "rejected_feedback",
    "active_ready",
}
_VALID_CASE_MEMORY_SAFETY_LABELS = {"customer_safe", "internal_only", "do_not_store"}
_CASE_MEMORY_LEDGER_SCHEMA_VERSION = "case-memory-ledger-v1"
_RETRYABLE_STORAGE_ERROR_SNIPPETS = (
    "connection timeout expired",
    "server closed the connection unexpectedly",
    "ssl error",
    "unexpected eof while reading",
    "consuming input failed",
)

_ResultT = TypeVar("_ResultT")
_VALID_MESSAGE_SENTIMENTS = {"good", "bad", "neutral"}
_TICKET_SCHEMA_VERSION_KEY = "ticket_flow_schema_version"
_TICKET_SCHEMA_VERSION = "2026-single-ai-managed-v9-product-selection-state"
_COMPATIBLE_INCREMENTAL_SCHEMA_VERSIONS = {
    "2026-single-ai-managed-v2",
    "2026-single-ai-managed-v3",
    "2026-single-ai-managed-v4",
    "2026-single-ai-managed-v5",
    "2026-single-ai-managed-v6",
    "2026-single-ai-managed-v7-client-agent-runtime",
    "2026-single-ai-managed-v8-message-meta",
    "2026-single-ai-managed-v9-product-selection-state",
}
_TICKET_MESSAGE_PERSISTED_FIELDS = {
    "role",
    "content",
    "created_at",
    "sentiment_label",
    "sources",
    "citations",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _normalize_status(value: Any) -> str:
    status = str(value or "open").strip().lower()
    if status == "waiting_for_engineer":
        return "investigating"
    return status if status in _VALID_STATUSES else "open"


def _normalize_assignment_status(value: Any, *, assigned_engineer_id: Any = None) -> str:
    status = str(value or "").strip().lower()
    if status in _VALID_ASSIGNMENT_STATUSES:
        return status
    return "assigned" if str(assigned_engineer_id or "").strip() else "pending"


def _normalize_dispatch_status(value: Any, *, assignment_status: Any = None) -> str:
    status = str(value or "").strip().lower()
    if status in _VALID_DISPATCH_STATUSES:
        return status
    normalized_assignment = _normalize_assignment_status(assignment_status)
    return "resolved" if normalized_assignment == "resolved" else normalized_assignment


def _normalize_previous_assignees(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        engineer_id = str(item or "").strip()
        if engineer_id and engineer_id not in normalized:
            normalized.append(engineer_id)
    return normalized


def _normalize_workspace_role(value: Any) -> str:
    role = str(value or "engineer").strip().lower()
    return role if role in _VALID_WORKSPACE_ROLES else "engineer"


def _normalize_engineer_availability(value: Any) -> str:
    availability = str(value or "unavailable").strip().lower()
    return availability if availability in _VALID_ENGINEER_AVAILABILITY else "unavailable"


def _normalize_workspace_account(account: dict[str, Any]) -> dict[str, Any]:
    account_id = str(account.get("account_id") or "").strip()
    if not account_id:
        raise ValueError("account_id is required")
    now = account.get("updated_at") or account.get("created_at") or _utc_now()
    return {
        "account_id": account_id,
        "display_name": str(account.get("display_name") or account_id).strip() or account_id,
        "role": _normalize_workspace_role(account.get("role")),
        "password_hash": str(account.get("password_hash") or "").strip(),
        "active": bool(account.get("active", True)),
        "availability": _normalize_engineer_availability(account.get("availability")),
        "availability_reason": str(account.get("availability_reason") or "").strip() or None,
        "availability_updated_at": account.get("availability_updated_at"),
        "last_assigned_at": account.get("last_assigned_at"),
        "created_at": account.get("created_at") or now,
        "updated_at": now,
    }


def _workspace_account_row_to_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "account_id": str(row[0]),
        "display_name": str(row[1]),
        "role": _normalize_workspace_role(row[2]),
        "password_hash": str(row[3] or ""),
        "active": bool(row[4]),
        "availability": _normalize_engineer_availability(row[5]),
        "availability_reason": str(row[6] or "").strip() or None,
        "availability_updated_at": _to_iso(row[7]) if row[7] is not None else None,
        "last_assigned_at": _to_iso(row[8]) if row[8] is not None else None,
        "created_at": _to_iso(row[9]),
        "updated_at": _to_iso(row[10]),
    }


def _safe_float_value(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _normalize_role(value: Any) -> str:
    role = str(value or "assistant").strip().lower()
    return role if role in _VALID_ROLES else "assistant"


def _normalize_investigation_role(value: Any) -> str:
    role = str(value or "engineer_ai").strip().lower()
    return role if role in _VALID_INVESTIGATION_ROLES else "engineer_ai"


def _normalize_investigation_state(value: Any) -> str:
    state = str(value or "active").strip().lower()
    return state if state in _VALID_INVESTIGATION_STATES else "active"


def _normalize_enum(value: Any, allowed: set[str], default: str) -> str:
    normalized = str(value or default).strip().lower()
    return normalized if normalized in allowed else default


def _derive_engineer_case_investigation_state(
    *,
    final_confirmation_requested_at: Any,
    closed_at: Any,
) -> str:
    if closed_at is not None:
        return "closed"
    if final_confirmation_requested_at is not None:
        return "awaiting_confirmation"
    return "active"


def _normalize_message_sentiment_label(value: Any) -> str | None:
    label = str(value or "").strip().lower()
    return label if label in _VALID_MESSAGE_SENTIMENTS else None


def _normalize_product(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _normalize_product_selection_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, Any] = copy.deepcopy(value)
    normalized["phase"] = str(normalized.get("phase") or "").strip().lower() or None
    normalized["pending_customer_message"] = " ".join(
        str(normalized.get("pending_customer_message") or "").split()
    ).strip() or None
    normalized["pending_message_created_at"] = (
        _to_iso(normalized.get("pending_message_created_at"))
        if normalized.get("pending_message_created_at") is not None
        else None
    )
    normalized["last_confirmation_requested_at"] = (
        _to_iso(normalized.get("last_confirmation_requested_at"))
        if normalized.get("last_confirmation_requested_at") is not None
        else None
    )
    normalized["last_updated_at"] = (
        _to_iso(normalized.get("last_updated_at"))
        if normalized.get("last_updated_at") is not None
        else None
    )
    if not normalized["phase"]:
        return None
    return normalized


def _normalize_client_intake_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, Any] = copy.deepcopy(value)
    normalized["phase"] = str(normalized.get("phase") or "").strip() or None
    normalized["product"] = _normalize_product(normalized.get("product"))
    normalized["issue_mode"] = str(normalized.get("issue_mode") or "").strip().lower() or None
    known_information = normalized.get("known_information")
    if isinstance(known_information, dict):
        normalized["known_information"] = {
            str(key or "").strip().lower(): " ".join(str(item or "").split()).strip()
            for key, item in known_information.items()
            if str(key or "").strip() and " ".join(str(item or "").split()).strip()
        }
    else:
        normalized["known_information"] = {}
    missing_information = normalized.get("missing_information")
    if isinstance(missing_information, list):
        normalized["missing_information"] = [
            str(item or "").strip().lower()
            for item in missing_information
            if str(item or "").strip()
        ]
    else:
        normalized["missing_information"] = []
    normalized["ready_for_engineer_ticket"] = bool(normalized.get("ready_for_engineer_ticket"))
    issue_timestamp_parts = normalized.get("issue_timestamp_parts")
    if isinstance(issue_timestamp_parts, dict):
        normalized["issue_timestamp_parts"] = {
            str(key or "").strip().lower(): " ".join(str(item or "").split()).strip()
            for key, item in issue_timestamp_parts.items()
            if str(key or "").strip() and " ".join(str(item or "").split()).strip()
        }
    else:
        normalized["issue_timestamp_parts"] = {}
    try:
        clarification_rounds_used = int(str(normalized.get("clarification_rounds_used") or "").strip())
    except (TypeError, ValueError):
        clarification_rounds_used = 0
    normalized["clarification_rounds_used"] = clarification_rounds_used if clarification_rounds_used >= 0 else 0
    normalized["last_updated_at"] = (
        _to_iso(normalized.get("last_updated_at"))
        if normalized.get("last_updated_at") is not None
        else None
    )
    return normalized


def _normalize_client_agent_runtime_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized = copy.deepcopy(value)
    normalized["runtime_version"] = str(normalized.get("runtime_version") or "").strip() or None
    normalized["active_run_id"] = str(normalized.get("active_run_id") or "").strip() or None
    normalized["product"] = _normalize_product(normalized.get("product"))
    normalized["message_id"] = str(normalized.get("message_id") or "").strip() or None
    normalized["workflow_action"] = str(normalized.get("workflow_action") or "").strip() or None
    normalized["status"] = str(normalized.get("status") or "").strip().lower() or None
    normalized["updated_at"] = _to_iso(normalized.get("updated_at")) if normalized.get("updated_at") is not None else None
    normalized["completed_at"] = _to_iso(normalized.get("completed_at")) if normalized.get("completed_at") is not None else None
    for agent_key in ("main_agent", "route_agent", "rag_agent", "review_agent"):
        agent_value = normalized.get(agent_key)
        if isinstance(agent_value, dict):
            normalized_agent = copy.deepcopy(agent_value)
            normalized_agent["phase"] = str(normalized_agent.get("phase") or "").strip() or None
            normalized_agent["status"] = str(normalized_agent.get("status") or "").strip().lower() or None
            normalized_agent["decision"] = str(normalized_agent.get("decision") or "").strip() or None
            normalized_agent["reason"] = str(normalized_agent.get("reason") or "").strip() or None
            normalized_agent["started_at"] = (
                _to_iso(normalized_agent.get("started_at"))
                if normalized_agent.get("started_at") is not None
                else None
            )
            normalized_agent["updated_at"] = (
                _to_iso(normalized_agent.get("updated_at"))
                if normalized_agent.get("updated_at") is not None
                else None
            )
            normalized_agent["completed_at"] = (
                _to_iso(normalized_agent.get("completed_at"))
                if normalized_agent.get("completed_at") is not None
                else None
            )
            normalized[agent_key] = normalized_agent
        else:
            normalized[agent_key] = {}
    return normalized


def _ticket_message_meta(message: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    meta: dict[str, Any] = {}
    raw_meta = message.get("meta")
    if isinstance(raw_meta, dict):
        meta.update(copy.deepcopy(raw_meta))
    for key, value in message.items():
        normalized_key = str(key or "").strip()
        if not normalized_key or normalized_key in _TICKET_MESSAGE_PERSISTED_FIELDS or normalized_key == "meta":
            continue
        meta[normalized_key] = copy.deepcopy(value)
    return meta


def _normalize_json_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [copy.deepcopy(item) for item in value if isinstance(item, dict)]


def _normalize_engineer_hitl_feedback(feedback: dict[str, Any]) -> dict[str, Any]:
    feedback_id = str(feedback.get("feedback_id") or "").strip()
    if not feedback_id:
        raise ValueError("feedback_id is required")
    engineer_case_id = str(feedback.get("engineer_case_id") or "").strip()
    if not engineer_case_id:
        raise ValueError("engineer_case_id is required")
    client_ticket_id = str(feedback.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise ValueError("client_ticket_id is required")
    created_by = str(feedback.get("created_by") or feedback.get("engineer_id") or "").strip()
    if not created_by:
        raise ValueError("created_by is required")

    normalized = copy.deepcopy(feedback)
    normalized["feedback_id"] = feedback_id
    normalized["engineer_case_id"] = engineer_case_id
    normalized["client_ticket_id"] = client_ticket_id
    normalized["run_id"] = str(normalized.get("run_id") or "").strip() or None
    normalized["message_id"] = str(normalized.get("message_id") or "").strip() or None
    normalized["evidence_packet_id"] = str(normalized.get("evidence_packet_id") or "").strip() or None
    normalized["feedback_type"] = _normalize_enum(
        normalized.get("feedback_type"),
        _VALID_HITL_FEEDBACK_TYPES,
        "approve",
    )
    normalized["diagnosis_correctness"] = _normalize_enum(
        normalized.get("diagnosis_correctness"),
        _VALID_HITL_DIAGNOSIS_CORRECTNESS,
        "not_applicable",
    )
    normalized["root_cause_correctness"] = _normalize_enum(
        normalized.get("root_cause_correctness"),
        _VALID_HITL_ROOT_CAUSE_CORRECTNESS,
        "unknown",
    )
    normalized["evidence_quality"] = _normalize_enum(
        normalized.get("evidence_quality"),
        _VALID_HITL_EVIDENCE_QUALITY,
        "insufficient",
    )
    normalized["citation_quality"] = _normalize_enum(
        normalized.get("citation_quality"),
        _VALID_HITL_CITATION_QUALITY,
        "not_applicable",
    )
    normalized["customer_reply_quality"] = _normalize_enum(
        normalized.get("customer_reply_quality"),
        _VALID_HITL_CUSTOMER_REPLY_QUALITY,
        "not_applicable",
    )
    normalized["missing_information"] = _normalize_json_list(normalized.get("missing_information"))
    normalized["incorrect_claims"] = _normalize_json_list(normalized.get("incorrect_claims"))
    normalized["corrected_root_cause"] = str(normalized.get("corrected_root_cause") or "").strip() or None
    normalized["corrected_solution"] = str(normalized.get("corrected_solution") or "").strip() or None
    normalized["corrected_customer_reply"] = (
        str(normalized.get("corrected_customer_reply") or "").strip() or None
    )
    normalized["evidence_refs"] = _normalize_json_list(normalized.get("evidence_refs"))
    normalized["memory_candidate"] = _normalize_enum(
        normalized.get("memory_candidate"),
        _VALID_HITL_MEMORY_CANDIDATE,
        "no",
    )
    normalized["memory_safety"] = _normalize_enum(
        normalized.get("memory_safety"),
        _VALID_HITL_MEMORY_SAFETY,
        "do_not_store",
    )
    normalized["memory_notes"] = str(normalized.get("memory_notes") or "").strip() or None
    for version_key in (
        "prompt_version",
        "workflow_version",
        "tool_policy_version",
        "rag_access_policy_version",
        "evidence_packet_version",
    ):
        normalized[version_key] = str(normalized.get(version_key) or "").strip() or None
    normalized["created_by"] = created_by
    normalized["created_at"] = normalized.get("created_at") or _utc_now()
    return normalized


def _normalize_case_memory_ledger(record: dict[str, Any]) -> dict[str, Any]:
    memory_record_id = str(record.get("memory_record_id") or "").strip()
    if not memory_record_id:
        raise ValueError("memory_record_id is required")
    source_feedback_id = str(record.get("source_feedback_id") or "").strip()
    if not source_feedback_id:
        raise ValueError("source_feedback_id is required")
    engineer_case_id = str(record.get("engineer_case_id") or "").strip()
    if not engineer_case_id:
        raise ValueError("engineer_case_id is required")
    client_ticket_id = str(record.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise ValueError("client_ticket_id is required")

    normalized = copy.deepcopy(record)
    normalized["memory_record_id"] = memory_record_id
    normalized["source_feedback_id"] = source_feedback_id
    normalized["engineer_case_id"] = engineer_case_id
    normalized["client_ticket_id"] = client_ticket_id
    normalized["feedback_type"] = _normalize_enum(
        normalized.get("feedback_type"),
        _VALID_HITL_FEEDBACK_TYPES,
        "approve",
    )
    normalized["ledger_status"] = _normalize_enum(
        normalized.get("ledger_status"),
        _VALID_CASE_MEMORY_LEDGER_STATUSES,
        "ledger_only",
    )
    normalized["retrieval_enabled"] = bool(normalized.get("retrieval_enabled"))
    normalized["active_memory_status"] = _normalize_enum(
        normalized.get("active_memory_status"),
        _VALID_CASE_MEMORY_ACTIVE_STATUSES,
        "inactive",
    )
    for key in (
        "symptom",
        "root_cause",
        "solution",
        "customer_safe_summary",
        "internal_only_summary",
        "prompt_version",
        "workflow_version",
        "tool_policy_version",
        "rag_access_policy_version",
        "evidence_packet_version",
    ):
        normalized[key] = str(normalized.get(key) or "").strip() or None
    normalized["evidence_refs"] = _normalize_json_list(normalized.get("evidence_refs"))
    normalized["safety_label"] = _normalize_enum(
        normalized.get("safety_label"),
        _VALID_CASE_MEMORY_SAFETY_LABELS,
        "do_not_store",
    )
    normalized["quality_label"] = _normalize_enum(
        normalized.get("quality_label"),
        _VALID_CASE_MEMORY_QUALITY_LABELS,
        "ledger_only",
    )
    normalized["memory_schema_version"] = (
        str(normalized.get("memory_schema_version") or "").strip()
        or _CASE_MEMORY_LEDGER_SCHEMA_VERSION
    )
    normalized["metadata"] = (
        copy.deepcopy(normalized.get("metadata")) if isinstance(normalized.get("metadata"), dict) else {}
    )
    normalized["created_at"] = normalized.get("created_at") or _utc_now()
    normalized["updated_at"] = normalized.get("updated_at") or normalized["created_at"]
    return normalized


def _safe_positive_int(value: Any, default_value: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default_value
    return parsed if parsed > 0 else default_value


def _safe_non_negative_int(value: Any, default_value: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default_value
    return parsed if parsed >= 0 else default_value


def _safe_positive_float(value: Any, default_value: float) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default_value
    return parsed if parsed > 0 else default_value


def _clean_error_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _is_retryable_storage_error(exc: BaseException) -> bool:
    if isinstance(exc, psycopg.OperationalError):
        return True
    message = _clean_error_text(exc).lower()
    return any(snippet in message for snippet in _RETRYABLE_STORAGE_ERROR_SNIPPETS)


def _case_sequence_from_identifiers(engineer_case_id: Any, case_sequence: Any) -> int:
    explicit = _safe_positive_int(case_sequence, 0)
    if explicit:
        return explicit
    text = str(engineer_case_id or "").strip()
    if "-" not in text:
        return 1
    suffix = text.rsplit("-", 1)[-1]
    return _safe_positive_int(suffix, 1)


def _ticket_client_reference(ticket: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ticket, dict):
        return {"ticket_id": "", "subject": ""}
    return {
        "ticket_id": str(ticket.get("ticket_id") or "").strip(),
        "subject": str(ticket.get("subject") or "").strip(),
        "status": _normalize_status(ticket.get("status")),
    }


def _build_case_investigation_view(
    investigation_id: str,
    *,
    state: Any,
    trigger_reason: Any,
    trigger_source: Any,
    draft_customer_reply: Any,
    final_confirmation_requested_at: Any,
    opened_at: Any,
    updated_at: Any,
    closed_at: Any,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": str(investigation_id or "").strip(),
        "state": _normalize_investigation_state(state),
        "trigger_reason": str(trigger_reason or "").strip(),
        "trigger_source": str(trigger_source or "").strip(),
        "draft_customer_reply": str(draft_customer_reply or "").strip(),
        "final_confirmation_requested_at": _to_iso(final_confirmation_requested_at)
        if final_confirmation_requested_at is not None
        else None,
        "opened_at": _to_iso(opened_at),
        "updated_at": _to_iso(updated_at),
        "closed_at": _to_iso(closed_at) if closed_at is not None else None,
        "messages": copy.deepcopy(messages),
    }


def _engineer_case_record_to_payload(
    engineer_case: dict[str, Any],
    *,
    client_ticket: dict[str, Any] | None,
    include_client_messages: bool,
) -> dict[str, Any]:
    internal_messages = (
        [copy.deepcopy(item) for item in engineer_case.get("messages", []) if isinstance(item, dict)]
        if isinstance(engineer_case.get("messages"), list)
        else []
    )
    investigation = _build_case_investigation_view(
        str(engineer_case.get("thread_id") or engineer_case.get("engineer_case_id") or ""),
        state=engineer_case.get("investigation_state"),
        trigger_reason=engineer_case.get("trigger_reason"),
        trigger_source=engineer_case.get("trigger_source"),
        draft_customer_reply=engineer_case.get("draft_customer_reply"),
        final_confirmation_requested_at=engineer_case.get("final_confirmation_requested_at"),
        opened_at=engineer_case.get("opened_at"),
        updated_at=engineer_case.get("updated_at"),
        closed_at=engineer_case.get("closed_at"),
        messages=internal_messages,
    )
    parent_ref = _ticket_client_reference(client_ticket)
    title = str(engineer_case.get("title") or "").strip() or parent_ref["subject"] or "Engineer case"
    client_messages = (
        [copy.deepcopy(item) for item in client_ticket.get("messages", []) if isinstance(item, dict)]
        if include_client_messages and isinstance(client_ticket, dict) and isinstance(client_ticket.get("messages"), list)
        else []
    )
    is_closed = investigation["state"] == "closed"
    assignment_status = _normalize_assignment_status(
        engineer_case.get("assignment_status"),
        assigned_engineer_id=engineer_case.get("assigned_engineer_id"),
    )
    return {
        "engineer_case_id": str(engineer_case.get("engineer_case_id") or "").strip(),
        "ticket_id": str(engineer_case.get("engineer_case_id") or "").strip(),
        "client_ticket_id": parent_ref["ticket_id"],
        "client_ticket_ref": parent_ref,
        "case_sequence": _case_sequence_from_identifiers(
            engineer_case.get("engineer_case_id"),
            engineer_case.get("case_sequence"),
        ),
        "title": title,
        "subject": title,
        "status": _normalize_status(engineer_case.get("status")),
        "client_status": _normalize_status((client_ticket or {}).get("status"))
        if isinstance(client_ticket, dict)
        else None,
        "assignment_status": assignment_status,
        "assigned_engineer_id": str(engineer_case.get("assigned_engineer_id") or "").strip() or None,
        "assigned_at": _to_iso(engineer_case.get("assigned_at")) if engineer_case.get("assigned_at") else None,
        "sla_due_at": _to_iso(engineer_case.get("sla_due_at")) if engineer_case.get("sla_due_at") else None,
        "assignment_attempt_count": _safe_non_negative_int(engineer_case.get("assignment_attempt_count"), 0),
        "previous_assignees": _normalize_previous_assignees(engineer_case.get("previous_assignees")),
        "last_assignment_reason": str(engineer_case.get("last_assignment_reason") or "").strip() or None,
        "dispatch_status": _normalize_dispatch_status(
            engineer_case.get("dispatch_status"), assignment_status=assignment_status
        ),
        "assignment_updated_at": (
            _to_iso(engineer_case.get("assignment_updated_at"))
            if engineer_case.get("assignment_updated_at")
            else None
        ),
        "assignment_version": _safe_non_negative_int(engineer_case.get("assignment_version"), 0),
        "trigger_source": str(engineer_case.get("trigger_source") or "").strip(),
        "trigger_reason": str(engineer_case.get("trigger_reason") or "").strip(),
        "requester": str((client_ticket or {}).get("requester") or "").strip(),
        "customer_id": str((client_ticket or {}).get("customer_id") or "").strip(),
        "last_engineer_action": (client_ticket or {}).get("last_engineer_action"),
        "created_at": _to_iso(engineer_case.get("opened_at")),
        "opened_at": _to_iso(engineer_case.get("opened_at")),
        "updated_at": _to_iso(engineer_case.get("updated_at")),
        "closed_at": _to_iso(engineer_case.get("closed_at"))
        if engineer_case.get("closed_at") is not None
        else None,
        "messages": client_messages,
        "active_investigation": None if is_closed else investigation,
        "investigation_history": [investigation] if is_closed else [],
        "engineer_handoff_packet": (
            copy.deepcopy(engineer_case.get("engineer_handoff_packet"))
            if isinstance(engineer_case.get("engineer_handoff_packet"), dict)
            else None
        ),
        "engineer_agent_state": (
            copy.deepcopy(engineer_case.get("engineer_agent_state"))
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else None
        ),
    }


def _engineer_case_record_to_header_payload(
    engineer_case: dict[str, Any],
    *,
    client_ticket: dict[str, Any] | None,
) -> dict[str, Any]:
    parent_ref = _ticket_client_reference(client_ticket)
    title = str(engineer_case.get("title") or "").strip() or parent_ref["subject"] or "Engineer case"
    assignment_status = _normalize_assignment_status(
        engineer_case.get("assignment_status"),
        assigned_engineer_id=engineer_case.get("assigned_engineer_id"),
    )
    return {
        "engineer_case_id": str(engineer_case.get("engineer_case_id") or "").strip(),
        "ticket_id": str(engineer_case.get("engineer_case_id") or "").strip(),
        "client_ticket_id": parent_ref["ticket_id"],
        "client_ticket_ref": parent_ref,
        "case_sequence": _case_sequence_from_identifiers(
            engineer_case.get("engineer_case_id"),
            engineer_case.get("case_sequence"),
        ),
        "title": title,
        "subject": title,
        "status": _normalize_status(engineer_case.get("status")),
        "client_status": _normalize_status((client_ticket or {}).get("status"))
        if isinstance(client_ticket, dict)
        else None,
        "assignment_status": assignment_status,
        "assigned_engineer_id": str(engineer_case.get("assigned_engineer_id") or "").strip() or None,
        "assigned_at": _to_iso(engineer_case.get("assigned_at")) if engineer_case.get("assigned_at") else None,
        "sla_due_at": _to_iso(engineer_case.get("sla_due_at")) if engineer_case.get("sla_due_at") else None,
        "assignment_attempt_count": _safe_non_negative_int(engineer_case.get("assignment_attempt_count"), 0),
        "previous_assignees": _normalize_previous_assignees(engineer_case.get("previous_assignees")),
        "last_assignment_reason": str(engineer_case.get("last_assignment_reason") or "").strip() or None,
        "dispatch_status": _normalize_dispatch_status(
            engineer_case.get("dispatch_status"), assignment_status=assignment_status
        ),
        "assignment_updated_at": (
            _to_iso(engineer_case.get("assignment_updated_at"))
            if engineer_case.get("assignment_updated_at")
            else None
        ),
        "assignment_version": _safe_non_negative_int(engineer_case.get("assignment_version"), 0),
        "trigger_source": str(engineer_case.get("trigger_source") or "").strip(),
        "trigger_reason": str(engineer_case.get("trigger_reason") or "").strip(),
        "requester": str((client_ticket or {}).get("requester") or "").strip(),
        "customer_id": str((client_ticket or {}).get("customer_id") or "").strip(),
        "last_engineer_action": (client_ticket or {}).get("last_engineer_action"),
        "created_at": _to_iso(engineer_case.get("opened_at")),
        "opened_at": _to_iso(engineer_case.get("opened_at")),
        "updated_at": _to_iso(engineer_case.get("updated_at")),
        "closed_at": _to_iso(engineer_case.get("closed_at"))
        if engineer_case.get("closed_at") is not None
        else None,
        "messages": [],
        "active_investigation": None,
        "investigation_history": [],
        "engineer_handoff_packet": None,
        "engineer_agent_state": None,
    }


class TicketRepository(Protocol):
    def initialize(self) -> None:
        ...

    def close(self) -> None:
        ...

    def storage_mode(self) -> str:
        ...

    def exists(self, ticket_id: str) -> bool:
        ...

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        ...

    def list_tickets(self, include_messages: bool = True) -> list[dict[str, Any]]:
        ...

    def save_ticket(
        self,
        ticket: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        ...

    def update_message_sentiment_label(
        self,
        *,
        ticket_id: str,
        role: str,
        content: str,
        created_at: str,
        sentiment_label: str,
    ) -> bool:
        ...

    def get_engineer_case(
        self,
        engineer_case_id: str,
        *,
        include_client_messages: bool = True,
    ) -> dict[str, Any] | None:
        ...

    def list_engineer_cases(
        self,
        *,
        include_client_messages: bool = True,
        include_investigation_messages: bool = True,
    ) -> list[dict[str, Any]]:
        ...

    def list_engineer_case_headers(self) -> list[dict[str, Any]]:
        ...

    def list_ticket_engineer_cases(
        self,
        ticket_id: str,
        *,
        include_client_messages: bool = True,
    ) -> list[dict[str, Any]]:
        ...

    def get_active_engineer_case(
        self,
        ticket_id: str,
        *,
        include_client_messages: bool = True,
    ) -> dict[str, Any] | None:
        ...

    def save_engineer_case(
        self,
        engineer_case: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        ...

    def claim_engineer_case(
        self,
        engineer_case_id: str,
        engineer_id: str,
        *,
        updated_at: str,
    ) -> bool:
        ...

    def update_engineer_case_assignment(
        self,
        engineer_case_id: str,
        *,
        expected_version: int | None,
        assignment_status: str,
        assigned_engineer_id: str | None,
        assigned_at: str | None,
        sla_due_at: str | None,
        reason: str,
        updated_at: str,
        actor: str,
        event_type: str,
        dispatch_status: str | None = None,
    ) -> dict[str, Any] | None:
        ...

    def save_workspace_account(self, account: dict[str, Any]) -> dict[str, Any]:
        ...

    def get_workspace_account(self, account_id: str) -> dict[str, Any] | None:
        ...

    def list_workspace_accounts(self) -> list[dict[str, Any]]:
        ...

    def set_engineer_availability(
        self,
        account_id: str,
        *,
        availability: str,
        reason: str | None,
        actor_id: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        ...

    def record_workspace_audit_event(
        self,
        event_type: str,
        *,
        actor_id: str,
        target_id: str | None,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        ...

    def list_workspace_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        ...

    def begin_idempotent_request(
        self,
        scope: str,
        idempotency_key: str,
        *,
        created_at: str,
    ) -> dict[str, Any]:
        ...

    def complete_idempotent_request(
        self,
        scope: str,
        idempotency_key: str,
        *,
        response_payload: dict[str, Any],
        updated_at: str,
    ) -> None:
        ...

    def record_rollout_event(
        self,
        counter_key: str,
        event_key: str,
        *,
        created_at: str,
    ) -> tuple[int, bool]:
        ...

    def record_engineer_case_event(
        self,
        engineer_case_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        ...

    def list_engineer_case_events(
        self,
        engineer_case_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ...

    def record_engineer_hitl_feedback(self, feedback: dict[str, Any]) -> dict[str, Any]:
        ...

    def list_engineer_hitl_feedback(
        self,
        engineer_case_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ...

    def record_case_memory_ledger(self, record: dict[str, Any]) -> dict[str, Any]:
        ...

    def list_case_memory_ledger(
        self,
        engineer_case_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ...

    def record_engineer_replay_eval_item(self, item: dict[str, Any]) -> dict[str, Any]:
        ...

    def list_engineer_replay_eval_items(
        self,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def get_engineer_replay_eval_item(self, eval_item_id: str) -> dict[str, Any] | None:
        ...

    def get_active_investigation(self, ticket_id: str) -> dict[str, Any] | None:
        ...

    def list_ticket_investigations(
        self,
        ticket_id: str,
        include_messages: bool = True,
    ) -> list[dict[str, Any]]:
        ...

    def save_investigation(
        self,
        ticket_id: str,
        investigation: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        ...

    def record_event(self, ticket_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        ...

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        ...

    def list_ticket_events(self, ticket_id: str, limit: int = 100) -> list[dict[str, Any]]:
        ...

    def record_ticket_agent_event(
        self,
        ticket_id: str,
        message_id: str | None,
        run_id: str,
        agent_name: str,
        phase: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        ...

    def list_ticket_agent_events(self, ticket_id: str, limit: int = 100) -> list[dict[str, Any]]:
        ...

    def get_trace_ticket_snapshot(
        self,
        ticket_id: str,
        *,
        event_limit: int = 100,
        message_created_at: str | None = None,
        include_messages: bool = False,
        message_limit: int = 0,
    ) -> dict[str, Any] | None:
        ...

    def save_billing_ticket(self, billing_ticket: dict[str, Any]) -> None:
        ...

    def get_billing_ticket(self, billing_ticket_id: str) -> dict[str, Any] | None:
        ...

    def get_billing_ticket_by_client_ticket_id(self, client_ticket_id: str) -> dict[str, Any] | None:
        ...

    def list_billing_tickets(
        self,
        limit: int = 30,
        review_status: str | None = None,
        offset: int = 0,
        automation_filter: str | None = None,
        route_errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        ...

    def count_billing_tickets(
        self,
        review_status: str | None = None,
        automation_filter: str | None = None,
        route_errors_only: bool = False,
    ) -> int:
        ...

    def save_billing_response_token(self, token: dict[str, Any]) -> None:
        ...

    def get_billing_response_token(self, token_hash: str) -> dict[str, Any] | None:
        ...

    def mark_billing_response_token_used(self, token_hash: str, used_at: str) -> bool:
        ...

    def save_billing_route_correction(self, correction: dict[str, Any]) -> None:
        ...

    def get_billing_route_correction(self, billing_ticket_id: str) -> dict[str, Any] | None:
        ...

    def get_billing_route_corrections_for_tickets(
        self, billing_ticket_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        ...

    def list_billing_route_corrections(self, limit: int = 100) -> list[dict[str, Any]]:
        ...

    def apply_billing_route_correction(
        self,
        *,
        billing_ticket_id: str,
        active_route: dict[str, Any],
        correction: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def mark_billing_route_reviewed(
        self,
        *,
        billing_ticket_id: str,
        review_status: str,
    ) -> dict[str, Any]:
        ...


class InMemoryTicketRepository:
    def __init__(self) -> None:
        self._tickets: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._agent_events: list[dict[str, Any]] = []
        self._investigations: dict[str, list[dict[str, Any]]] = {}
        self._engineer_cases: dict[str, dict[str, Any]] = {}
        self._engineer_case_events: list[dict[str, Any]] = []
        self._engineer_hitl_feedback: list[dict[str, Any]] = []
        self._case_memory_ledger: list[dict[str, Any]] = []
        self._engineer_replay_eval_items: dict[str, dict[str, Any]] = {}
        self._billing_tickets: dict[str, dict[str, Any]] = {}
        self._billing_response_tokens: dict[str, dict[str, Any]] = {}
        self._billing_route_corrections: dict[str, dict[str, Any]] = {}
        self._assignment_lock = threading.RLock()
        self._workspace_accounts: dict[str, dict[str, Any]] = {}
        self._workspace_audit_events: list[dict[str, Any]] = []
        self._idempotency_records: dict[tuple[str, str], dict[str, Any]] = {}
        self._rollout_counters: dict[str, int] = {}
        self._rollout_events: dict[tuple[str, str], int] = {}

    def initialize(self) -> None:
        return None

    def close(self) -> None:
        return None

    def storage_mode(self) -> str:
        return "memory"

    def exists(self, ticket_id: str) -> bool:
        return ticket_id in self._tickets

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            return None
        item = copy.deepcopy(ticket)
        item["status"] = _normalize_status(item.get("status"))
        item["active_engineer_case_id"] = str(item.get("active_engineer_case_id") or "").strip() or None
        item["engineer_case_count"] = _safe_non_negative_int(item.get("engineer_case_count"), 0)
        item["product"] = _normalize_product(item.get("product"))
        item["product_selection_state"] = _normalize_product_selection_state(item.get("product_selection_state"))
        item["client_intake_state"] = _normalize_client_intake_state(item.get("client_intake_state"))
        item["client_agent_runtime_state"] = _normalize_client_agent_runtime_state(item.get("client_agent_runtime_state"))
        if not isinstance(item.get("messages"), list):
            item["messages"] = []
        return item

    def list_tickets(self, include_messages: bool = True) -> list[dict[str, Any]]:
        tickets: list[dict[str, Any]] = []
        for ticket in self._tickets.values():
            item = self.get_ticket(str(ticket.get("ticket_id") or "")) or copy.deepcopy(ticket)
            if not include_messages:
                item["messages"] = []
            tickets.append(item)
        return tickets

    def save_ticket(
        self,
        ticket: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        _ = new_messages  # Kept for interface compatibility.
        ticket_id = str(ticket.get("ticket_id", "")).strip()
        if not ticket_id:
            raise ValueError("ticket_id is required")
        saved_ticket = copy.deepcopy(ticket)
        saved_ticket["status"] = _normalize_status(saved_ticket.get("status"))
        saved_ticket["active_engineer_case_id"] = (
            str(saved_ticket.get("active_engineer_case_id") or "").strip() or None
        )
        saved_ticket["engineer_case_count"] = _safe_non_negative_int(
            saved_ticket.get("engineer_case_count"),
            0,
        )
        saved_ticket["product"] = _normalize_product(saved_ticket.get("product"))
        saved_ticket["product_selection_state"] = _normalize_product_selection_state(
            saved_ticket.get("product_selection_state")
        )
        saved_ticket["client_intake_state"] = _normalize_client_intake_state(saved_ticket.get("client_intake_state"))
        saved_ticket["client_agent_runtime_state"] = _normalize_client_agent_runtime_state(
            saved_ticket.get("client_agent_runtime_state")
        )
        if not isinstance(saved_ticket.get("messages"), list):
            saved_ticket["messages"] = []
        self._tickets[ticket_id] = saved_ticket

        investigations: list[dict[str, Any]] = []
        active = saved_ticket.get("active_investigation")
        if isinstance(active, dict):
            investigations.append(copy.deepcopy(active))
        history = saved_ticket.get("investigation_history")
        if isinstance(history, list):
            investigations.extend(copy.deepcopy(item) for item in history if isinstance(item, dict))
        if investigations:
            self._investigations[ticket_id] = investigations
            self._backfill_engineer_cases_from_legacy_ticket(ticket_id)

    def update_message_sentiment_label(
        self,
        *,
        ticket_id: str,
        role: str,
        content: str,
        created_at: str,
        sentiment_label: str,
    ) -> bool:
        ticket = self._tickets.get(str(ticket_id).strip())
        if ticket is None:
            return False
        normalized_label = _normalize_message_sentiment_label(sentiment_label)
        if normalized_label is None:
            return False
        expected_role = _normalize_role(role)
        expected_content = str(content).strip()
        expected_created_at = str(created_at).strip()
        messages = ticket.get("messages")
        if not isinstance(messages, list):
            return False
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if _normalize_role(message.get("role")) != expected_role:
                continue
            if str(message.get("content") or "").strip() != expected_content:
                continue
            if str(message.get("created_at") or "").strip() != expected_created_at:
                continue
            if _normalize_message_sentiment_label(message.get("sentiment_label")) == normalized_label:
                return False
            message["sentiment_label"] = normalized_label
            ticket["updated_at"] = ticket.get("updated_at") or _utc_now()
            return True
        return False

    def _normalize_engineer_case_record(self, engineer_case: dict[str, Any]) -> dict[str, Any]:
        engineer_case_id = str(engineer_case.get("engineer_case_id") or "").strip()
        if not engineer_case_id:
            raise ValueError("engineer_case_id is required")
        client_ticket_id = str(engineer_case.get("client_ticket_id") or "").strip()
        if not client_ticket_id:
            raise ValueError("client_ticket_id is required")
        opened_at = engineer_case.get("opened_at") or _utc_now()
        updated_at = engineer_case.get("updated_at") or opened_at
        normalized = copy.deepcopy(engineer_case)
        normalized["engineer_case_id"] = engineer_case_id
        normalized["client_ticket_id"] = client_ticket_id
        normalized["case_sequence"] = _case_sequence_from_identifiers(
            engineer_case_id,
            engineer_case.get("case_sequence"),
        )
        normalized["title"] = str(normalized.get("title") or "").strip() or "Engineer case"
        normalized["status"] = _normalize_status(normalized.get("status"))
        normalized["assigned_engineer_id"] = (
            str(normalized.get("assigned_engineer_id") or "").strip() or None
        )
        normalized["assignment_status"] = _normalize_assignment_status(
            normalized.get("assignment_status"),
            assigned_engineer_id=normalized.get("assigned_engineer_id"),
        )
        if normalized["status"] == "resolved" or normalized.get("closed_at") is not None:
            normalized["assignment_status"] = "resolved"
        normalized["assigned_at"] = normalized.get("assigned_at")
        normalized["sla_due_at"] = normalized.get("sla_due_at")
        normalized["assignment_attempt_count"] = _safe_non_negative_int(
            normalized.get("assignment_attempt_count"), 0
        )
        normalized["previous_assignees"] = _normalize_previous_assignees(
            normalized.get("previous_assignees")
        )
        normalized["last_assignment_reason"] = (
            str(normalized.get("last_assignment_reason") or "").strip() or None
        )
        normalized["dispatch_status"] = _normalize_dispatch_status(
            normalized.get("dispatch_status"),
            assignment_status=normalized.get("assignment_status"),
        )
        normalized["assignment_updated_at"] = normalized.get("assignment_updated_at")
        normalized["assignment_version"] = _safe_non_negative_int(
            normalized.get("assignment_version"), 0
        )
        normalized["trigger_source"] = str(normalized.get("trigger_source") or "").strip() or "support_query"
        normalized["trigger_reason"] = str(normalized.get("trigger_reason") or "").strip() or "unknown"
        normalized["investigation_state"] = _normalize_investigation_state(
            normalized.get("investigation_state")
        )
        normalized["draft_customer_reply"] = str(normalized.get("draft_customer_reply") or "").strip()
        normalized["final_confirmation_requested_at"] = normalized.get("final_confirmation_requested_at")
        normalized["engineer_handoff_packet"] = (
            copy.deepcopy(normalized.get("engineer_handoff_packet"))
            if isinstance(normalized.get("engineer_handoff_packet"), dict)
            else None
        )
        normalized["engineer_agent_state"] = (
            copy.deepcopy(normalized.get("engineer_agent_state"))
            if isinstance(normalized.get("engineer_agent_state"), dict)
            else None
        )
        normalized["opened_at"] = opened_at
        normalized["updated_at"] = updated_at
        normalized["closed_at"] = normalized.get("closed_at")
        normalized["messages"] = (
            [copy.deepcopy(item) for item in normalized.get("messages", []) if isinstance(item, dict)]
            if isinstance(normalized.get("messages"), list)
            else []
        )
        return normalized

    def _backfill_engineer_cases_from_legacy_ticket(self, ticket_id: str) -> None:
        normalized_ticket_id = str(ticket_id or "").strip()
        if not normalized_ticket_id:
            return
        if any(
            str(item.get("client_ticket_id") or "").strip() == normalized_ticket_id
            for item in self._engineer_cases.values()
        ):
            return
        ticket = self._tickets.get(normalized_ticket_id)
        if not isinstance(ticket, dict):
            return
        investigations = self.list_ticket_investigations(normalized_ticket_id, include_messages=True)
        if not investigations:
            return
        investigations = sorted(
            investigations,
            key=lambda item: str(item.get("opened_at") or item.get("updated_at") or ""),
        )
        for index, investigation in enumerate(investigations, start=1):
            engineer_case_id = f"{normalized_ticket_id}-{index}"
            record = {
                "engineer_case_id": engineer_case_id,
                "client_ticket_id": normalized_ticket_id,
                "case_sequence": index,
                "title": str(ticket.get("subject") or "Engineer case").strip() or "Engineer case",
                "status": (
                    "investigating"
                    if _normalize_investigation_state(investigation.get("state")) != "closed"
                    else _normalize_status(ticket.get("status"))
                ),
                "trigger_source": investigation.get("trigger_source"),
                "trigger_reason": investigation.get("trigger_reason"),
                "investigation_state": investigation.get("state"),
                "draft_customer_reply": investigation.get("draft_customer_reply"),
                "final_confirmation_requested_at": investigation.get("final_confirmation_requested_at"),
                "engineer_handoff_packet": (
                    copy.deepcopy(ticket.get("engineer_handoff_packet"))
                    if isinstance(ticket.get("engineer_handoff_packet"), dict)
                    else None
                ),
                "engineer_agent_state": (
                    copy.deepcopy(ticket.get("engineer_agent_state"))
                    if isinstance(ticket.get("engineer_agent_state"), dict)
                    else None
                ),
                "opened_at": investigation.get("opened_at"),
                "updated_at": investigation.get("updated_at"),
                "closed_at": investigation.get("closed_at"),
                "messages": investigation.get("messages") or [],
            }
            self._engineer_cases[engineer_case_id] = self._normalize_engineer_case_record(record)
        ticket["engineer_case_count"] = len(investigations)
        active = next(
            (
                case
                for case in self._engineer_cases.values()
                if str(case.get("client_ticket_id") or "").strip() == normalized_ticket_id
                and _normalize_investigation_state(case.get("investigation_state")) != "closed"
            ),
            None,
        )
        ticket["active_engineer_case_id"] = (
            str(active.get("engineer_case_id") or "").strip() if isinstance(active, dict) else None
        )

    def get_engineer_case(
        self,
        engineer_case_id: str,
        *,
        include_client_messages: bool = True,
    ) -> dict[str, Any] | None:
        self._backfill_engineer_cases_from_legacy_ticket(
            str(engineer_case_id).rsplit("-", 1)[0] if "-" in str(engineer_case_id) else engineer_case_id
        )
        engineer_case = self._engineer_cases.get(str(engineer_case_id).strip())
        if engineer_case is None:
            return None
        client_ticket = self.get_ticket(str(engineer_case.get("client_ticket_id") or ""))
        return _engineer_case_record_to_payload(
            engineer_case,
            client_ticket=client_ticket,
            include_client_messages=include_client_messages,
        )

    def list_engineer_cases(
        self,
        *,
        include_client_messages: bool = True,
        include_investigation_messages: bool = True,
    ) -> list[dict[str, Any]]:
        case_payloads: list[dict[str, Any]] = []
        for ticket_id in list(self._tickets):
            self._backfill_engineer_cases_from_legacy_ticket(ticket_id)
        for engineer_case in self._engineer_cases.values():
            client_ticket = self.get_ticket(str(engineer_case.get("client_ticket_id") or ""))
            payload_engineer_case = copy.deepcopy(engineer_case)
            if not include_investigation_messages:
                payload_engineer_case["messages"] = []
            case_payloads.append(
                _engineer_case_record_to_payload(
                    payload_engineer_case,
                    client_ticket=client_ticket,
                    include_client_messages=include_client_messages,
                )
            )
        return case_payloads

    def list_engineer_case_headers(self) -> list[dict[str, Any]]:
        case_payloads: list[dict[str, Any]] = []
        for ticket_id in list(self._tickets):
            self._backfill_engineer_cases_from_legacy_ticket(ticket_id)
        for engineer_case in self._engineer_cases.values():
            client_ticket = self.get_ticket(str(engineer_case.get("client_ticket_id") or ""))
            case_payloads.append(
                _engineer_case_record_to_header_payload(
                    engineer_case,
                    client_ticket=client_ticket,
                )
            )
        return case_payloads

    def list_ticket_engineer_cases(
        self,
        ticket_id: str,
        *,
        include_client_messages: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_ticket_id = str(ticket_id).strip()
        self._backfill_engineer_cases_from_legacy_ticket(normalized_ticket_id)
        rows = [
            item
            for item in self.list_engineer_cases(include_client_messages=include_client_messages)
            if str(item.get("client_ticket_id") or "").strip() == normalized_ticket_id
        ]
        rows.sort(
            key=lambda item: _safe_non_negative_int(item.get("case_sequence"), 0),
            reverse=True,
        )
        return rows

    def get_active_engineer_case(
        self,
        ticket_id: str,
        *,
        include_client_messages: bool = True,
    ) -> dict[str, Any] | None:
        normalized_ticket_id = str(ticket_id).strip()
        self._backfill_engineer_cases_from_legacy_ticket(normalized_ticket_id)
        active_case = next(
            (
                copy.deepcopy(item)
                for item in self._engineer_cases.values()
                if str(item.get("client_ticket_id") or "").strip() == normalized_ticket_id
                and _normalize_investigation_state(item.get("investigation_state")) != "closed"
            ),
            None,
        )
        if active_case is None:
            return None
        client_ticket = self.get_ticket(normalized_ticket_id)
        return _engineer_case_record_to_payload(
            active_case,
            client_ticket=client_ticket,
            include_client_messages=include_client_messages,
        )

    def save_engineer_case(
        self,
        engineer_case: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        existing = self._engineer_cases.get(str(engineer_case.get("engineer_case_id") or "").strip())
        if isinstance(existing, dict):
            assignment_fields = (
                "assigned_engineer_id",
                "assignment_status",
                "assigned_at",
                "sla_due_at",
                "assignment_attempt_count",
                "previous_assignees",
                "last_assignment_reason",
                "dispatch_status",
                "assignment_updated_at",
                "assignment_version",
            )
            engineer_case = dict(engineer_case)
            for field in assignment_fields:
                engineer_case[field] = copy.deepcopy(existing.get(field))
        saved = self._normalize_engineer_case_record(engineer_case)
        if isinstance(existing, dict):
            for field in assignment_fields:
                saved[field] = copy.deepcopy(existing.get(field))
        if new_messages:
            existing_ids = {str(item.get("id") or "").strip() for item in saved["messages"]}
            for item in new_messages:
                message_id = str(item.get("id") or "").strip()
                if message_id and message_id not in existing_ids:
                    saved["messages"].append(copy.deepcopy(item))
                    existing_ids.add(message_id)
        self._engineer_cases[str(saved["engineer_case_id"])] = saved
        ticket = self._tickets.get(str(saved.get("client_ticket_id") or "").strip())
        if isinstance(ticket, dict):
            ticket["engineer_case_count"] = max(
                _safe_non_negative_int(ticket.get("engineer_case_count"), 0),
                _case_sequence_from_identifiers(saved.get("engineer_case_id"), saved.get("case_sequence")),
            )
            if _normalize_investigation_state(saved.get("investigation_state")) == "closed":
                if str(ticket.get("active_engineer_case_id") or "").strip() == str(saved.get("engineer_case_id") or "").strip():
                    ticket["active_engineer_case_id"] = None
            else:
                ticket["active_engineer_case_id"] = str(saved.get("engineer_case_id") or "").strip() or None

    def claim_engineer_case(
        self,
        engineer_case_id: str,
        engineer_id: str,
        *,
        updated_at: str,
    ) -> bool:
        normalized_case_id = str(engineer_case_id or "").strip()
        normalized_engineer_id = str(engineer_id or "").strip()
        engineer_case = self._engineer_cases.get(normalized_case_id)
        if not isinstance(engineer_case, dict) or not normalized_engineer_id:
            return False
        current_engineer_id = str(engineer_case.get("assigned_engineer_id") or "").strip()
        if _normalize_status(engineer_case.get("status")) == "resolved":
            return False
        if current_engineer_id and current_engineer_id != normalized_engineer_id:
            return False
        engineer_case["assigned_engineer_id"] = normalized_engineer_id
        engineer_case["updated_at"] = updated_at
        return True

    def update_engineer_case_assignment(
        self,
        engineer_case_id: str,
        *,
        expected_version: int | None,
        assignment_status: str,
        assigned_engineer_id: str | None,
        assigned_at: str | None,
        sla_due_at: str | None,
        reason: str,
        updated_at: str,
        actor: str,
        event_type: str,
        dispatch_status: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_case_id = str(engineer_case_id or "").strip()
        normalized_status = _normalize_assignment_status(assignment_status)
        normalized_engineer_id = str(assigned_engineer_id or "").strip() or None
        if not normalized_case_id:
            return None
        if normalized_status == "assigned" and not normalized_engineer_id:
            raise ValueError("assigned_engineer_id is required for assigned cases")
        with self._assignment_lock:
            engineer_case = self._engineer_cases.get(normalized_case_id)
            if not isinstance(engineer_case, dict):
                return None
            current_version = _safe_non_negative_int(engineer_case.get("assignment_version"), 0)
            if expected_version is not None and current_version != expected_version:
                return None
            previous_engineer_id = str(engineer_case.get("assigned_engineer_id") or "").strip() or None
            previous_assigned_at = engineer_case.get("assigned_at")
            previous_assignment_status = _normalize_assignment_status(
                engineer_case.get("assignment_status"), assigned_engineer_id=previous_engineer_id
            )
            previous_assignees = _normalize_previous_assignees(engineer_case.get("previous_assignees"))
            if (
                previous_engineer_id
                and previous_engineer_id != normalized_engineer_id
                and previous_engineer_id not in previous_assignees
            ):
                previous_assignees.append(previous_engineer_id)
            engineer_case.update(
                {
                    "assignment_status": normalized_status,
                    "assigned_engineer_id": normalized_engineer_id,
                    "assigned_at": (
                        assigned_at
                        if normalized_status == "assigned"
                        else previous_assigned_at
                        if normalized_status == "resolved"
                        else None
                    ),
                    "sla_due_at": sla_due_at if normalized_status == "assigned" else None,
                    "assignment_attempt_count": _safe_non_negative_int(
                        engineer_case.get("assignment_attempt_count"), 0
                    ) + (1 if normalized_status == "assigned" else 0),
                    "previous_assignees": previous_assignees,
                    "last_assignment_reason": str(reason or "").strip() or "assignment_update",
                    "dispatch_status": _normalize_dispatch_status(
                        dispatch_status, assignment_status=normalized_status
                    ),
                    "assignment_updated_at": updated_at,
                    "assignment_version": current_version + 1,
                    "updated_at": updated_at,
                }
            )
            event = {
                "event": str(event_type or "engineer_case_assignment_changed"),
                "engineer_case_id": normalized_case_id,
                "actor": str(actor or "system").strip() or "system",
                "reason": str(reason or "").strip() or "assignment_update",
                "previous_assignment_status": previous_assignment_status,
                "assignment_status": normalized_status,
                "previous_assigned_engineer_id": previous_engineer_id,
                "assigned_engineer_id": normalized_engineer_id,
                "assignment_version": current_version + 1,
                "created_at": updated_at,
            }
            self.record_engineer_case_event(normalized_case_id, event["event"], event)
        return self.get_engineer_case(normalized_case_id, include_client_messages=False)

    def save_workspace_account(self, account: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_workspace_account(account)
        existing = self._workspace_accounts.get(normalized["account_id"])
        if isinstance(existing, dict):
            normalized["created_at"] = existing.get("created_at") or normalized["created_at"]
            if not normalized["password_hash"]:
                normalized["password_hash"] = str(existing.get("password_hash") or "")
            if "availability" not in account:
                normalized["availability"] = existing.get("availability") or "unavailable"
                normalized["availability_reason"] = existing.get("availability_reason")
                normalized["availability_updated_at"] = existing.get("availability_updated_at")
            normalized["last_assigned_at"] = account.get("last_assigned_at", existing.get("last_assigned_at"))
        if not normalized["password_hash"]:
            raise ValueError("password_hash is required")
        self._workspace_accounts[normalized["account_id"]] = copy.deepcopy(normalized)
        return copy.deepcopy(normalized)

    def get_workspace_account(self, account_id: str) -> dict[str, Any] | None:
        account = self._workspace_accounts.get(str(account_id or "").strip())
        return copy.deepcopy(account) if isinstance(account, dict) else None

    def list_workspace_accounts(self) -> list[dict[str, Any]]:
        accounts = [copy.deepcopy(item) for item in self._workspace_accounts.values()]
        accounts.sort(key=lambda item: (str(item.get("display_name") or "").lower(), item["account_id"]))
        return accounts

    def set_engineer_availability(
        self,
        account_id: str,
        *,
        availability: str,
        reason: str | None,
        actor_id: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        normalized_account_id = str(account_id or "").strip()
        normalized_availability = _normalize_engineer_availability(availability)
        with self._assignment_lock:
            account = self._workspace_accounts.get(normalized_account_id)
            if not isinstance(account, dict) or account.get("role") != "engineer":
                return None
            previous_availability = account.get("availability")
            previous_reason = account.get("availability_reason")
            account.update(
                {
                    "availability": normalized_availability,
                    "availability_reason": str(reason or "").strip() or None,
                    "availability_updated_at": updated_at,
                    "updated_at": updated_at,
                }
            )
            self.record_workspace_audit_event(
                "engineer_availability_changed",
                actor_id=actor_id,
                target_id=normalized_account_id,
                payload={
                    "previous_availability": previous_availability,
                    "availability": normalized_availability,
                    "previous_reason": previous_reason,
                    "reason": account["availability_reason"],
                },
                created_at=updated_at,
            )
            return copy.deepcopy(account)

    def record_workspace_audit_event(
        self,
        event_type: str,
        *,
        actor_id: str,
        target_id: str | None,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        self._workspace_audit_events.append(
            {
                "event_type": str(event_type or "workspace_event").strip() or "workspace_event",
                "actor_id": str(actor_id or "system").strip() or "system",
                "target_id": str(target_id or "").strip() or None,
                "payload": copy.deepcopy(payload),
                "created_at": created_at,
            }
        )

    def list_workspace_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 100), 1000))
        return [copy.deepcopy(item) for item in reversed(self._workspace_audit_events[-safe_limit:])]

    def begin_idempotent_request(
        self,
        scope: str,
        idempotency_key: str,
        *,
        created_at: str,
    ) -> dict[str, Any]:
        normalized_key = (str(scope or "").strip(), str(idempotency_key or "").strip())
        if not all(normalized_key):
            raise ValueError("scope and idempotency_key are required")
        with self._assignment_lock:
            existing = self._idempotency_records.get(normalized_key)
            if isinstance(existing, dict):
                return {**copy.deepcopy(existing), "created": False}
            record = {
                "scope": normalized_key[0],
                "idempotency_key": normalized_key[1],
                "state": "processing",
                "response_payload": None,
                "created_at": created_at,
                "updated_at": created_at,
            }
            self._idempotency_records[normalized_key] = record
            return {**copy.deepcopy(record), "created": True}

    def complete_idempotent_request(
        self,
        scope: str,
        idempotency_key: str,
        *,
        response_payload: dict[str, Any],
        updated_at: str,
    ) -> None:
        normalized_key = (str(scope or "").strip(), str(idempotency_key or "").strip())
        with self._assignment_lock:
            record = self._idempotency_records.get(normalized_key)
            if not isinstance(record, dict):
                raise ValueError("idempotency request was not started")
            record.update(
                {
                    "state": "completed",
                    "response_payload": copy.deepcopy(response_payload),
                    "updated_at": updated_at,
                }
            )

    def record_rollout_event(
        self,
        counter_key: str,
        event_key: str,
        *,
        created_at: str,
    ) -> tuple[int, bool]:
        del created_at
        normalized_counter_key = str(counter_key or "").strip()
        normalized_event_key = str(event_key or "").strip()
        if not normalized_counter_key or not normalized_event_key:
            raise ValueError("counter_key and event_key are required")
        key = (normalized_counter_key, normalized_event_key)
        with self._assignment_lock:
            if key in self._rollout_events:
                return self._rollout_events[key], False
            position = self._rollout_counters.get(normalized_counter_key, 0) + 1
            self._rollout_counters[normalized_counter_key] = position
            self._rollout_events[key] = position
            return position, True

    def record_engineer_case_event(
        self,
        engineer_case_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        created_at = payload.get("created_at") or _utc_now()
        self._engineer_case_events.append(
            {
                "engineer_case_id": engineer_case_id,
                "event_type": event_type,
                "payload": copy.deepcopy(payload),
                "created_at": created_at,
            }
        )

    def list_engineer_case_events(
        self,
        engineer_case_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        normalized_case_id = str(engineer_case_id).strip()
        filtered = [
            item
            for item in reversed(self._engineer_case_events)
            if str(item.get("engineer_case_id") or "").strip() == normalized_case_id
        ]
        return [copy.deepcopy(item) for item in filtered[:safe_limit]]

    def record_engineer_hitl_feedback(self, feedback: dict[str, Any]) -> dict[str, Any]:
        saved = _normalize_engineer_hitl_feedback(feedback)
        self._engineer_hitl_feedback = [
            item
            for item in self._engineer_hitl_feedback
            if str(item.get("feedback_id") or "").strip() != str(saved["feedback_id"])
        ]
        self._engineer_hitl_feedback.append(saved)
        return copy.deepcopy(saved)

    def list_engineer_hitl_feedback(
        self,
        engineer_case_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        normalized_case_id = str(engineer_case_id).strip()
        rows = [
            item
            for item in reversed(self._engineer_hitl_feedback)
            if str(item.get("engineer_case_id") or "").strip() == normalized_case_id
        ]
        return [copy.deepcopy(item) for item in rows[:safe_limit]]

    def record_case_memory_ledger(self, record: dict[str, Any]) -> dict[str, Any]:
        saved = _normalize_case_memory_ledger(record)
        self._case_memory_ledger = [
            item
            for item in self._case_memory_ledger
            if str(item.get("memory_record_id") or "").strip() != str(saved["memory_record_id"])
        ]
        self._case_memory_ledger.append(saved)
        return copy.deepcopy(saved)

    def list_case_memory_ledger(
        self,
        engineer_case_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        normalized_case_id = str(engineer_case_id).strip()
        rows = [
            item
            for item in reversed(self._case_memory_ledger)
            if str(item.get("engineer_case_id") or "").strip() == normalized_case_id
        ]
        return [copy.deepcopy(item) for item in rows[:safe_limit]]

    def record_engineer_replay_eval_item(self, item: dict[str, Any]) -> dict[str, Any]:
        saved = copy.deepcopy(item)
        saved.setdefault("created_at", _utc_now())
        saved.setdefault("updated_at", _utc_now())
        saved["dataset_status"] = str(saved.get("dataset_status") or "candidate").strip().lower()
        eval_item_id = str(saved.get("eval_item_id") or "").strip()
        if not eval_item_id:
            raise ValueError("eval_item_id is required")
        self._engineer_replay_eval_items[eval_item_id] = saved
        return copy.deepcopy(saved)

    def list_engineer_replay_eval_items(
        self,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        rows: list[dict[str, Any]] = []
        for item in self._engineer_replay_eval_items.values():
            if status is not None and str(item.get("dataset_status") or "").strip().lower() != status.strip().lower():
                continue
            rows.append(copy.deepcopy(item))
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows[:safe_limit]

    def get_engineer_replay_eval_item(self, eval_item_id: str) -> dict[str, Any] | None:
        item = self._engineer_replay_eval_items.get(str(eval_item_id).strip())
        return copy.deepcopy(item) if item is not None else None

    def get_active_investigation(self, ticket_id: str) -> dict[str, Any] | None:
        investigations = self._investigations.get(ticket_id, [])
        for item in investigations:
            if str(item.get("state") or "").strip().lower() != "closed":
                return copy.deepcopy(item)
        return None

    def list_ticket_investigations(
        self,
        ticket_id: str,
        include_messages: bool = True,
    ) -> list[dict[str, Any]]:
        investigations = self._investigations.get(str(ticket_id).strip(), [])
        rows: list[dict[str, Any]] = []
        for item in investigations:
            copied = copy.deepcopy(item)
            if not include_messages:
                copied["messages"] = []
            copied["state"] = str(copied.get("state") or "active").strip().lower()
            copied["draft_customer_reply"] = str(copied.get("draft_customer_reply") or "").strip()
            copied["final_confirmation_requested_at"] = copied.get("final_confirmation_requested_at")
            rows.append(copied)
        rows.sort(
            key=lambda item: (
                str(item.get("state") or "").lower() == "closed",
                str(item.get("updated_at") or item.get("opened_at") or ""),
            ),
            reverse=False,
        )
        return rows

    def save_investigation(
        self,
        ticket_id: str,
        investigation: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        normalized_ticket_id = str(ticket_id).strip()
        if not normalized_ticket_id:
            raise ValueError("ticket_id is required")
        investigation_id = str(investigation.get("id") or "").strip()
        if not investigation_id:
            raise ValueError("investigation.id is required")

        saved = copy.deepcopy(investigation)
        saved["state"] = str(saved.get("state") or "active").strip().lower()
        saved["draft_customer_reply"] = str(saved.get("draft_customer_reply") or "").strip()
        saved.setdefault("messages", [])
        if new_messages:
            existing_ids = {str(item.get("id") or "").strip() for item in saved["messages"]}
            for item in new_messages:
                message_id = str(item.get("id") or "").strip()
                if message_id and message_id not in existing_ids:
                    saved["messages"].append(copy.deepcopy(item))
                    existing_ids.add(message_id)

        investigations = self._investigations.setdefault(normalized_ticket_id, [])
        for index, current in enumerate(investigations):
            if str(current.get("id") or "").strip() == investigation_id:
                investigations[index] = saved
                break
        else:
            investigations.append(saved)

        ticket = self._tickets.get(normalized_ticket_id)
        if ticket is not None:
            ticket["active_investigation"] = (
                copy.deepcopy(saved) if saved["state"] != "closed" else None
            )
            history = [
                copy.deepcopy(item)
                for item in investigations
                if str(item.get("state") or "").strip().lower() == "closed"
            ]
            ticket["investigation_history"] = history

    def record_event(self, ticket_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        created_at = payload.get("created_at") or _utc_now()
        self._events.append(
            {
                "ticket_id": ticket_id,
                "event_type": event_type,
                "payload": copy.deepcopy(payload),
                "created_at": created_at,
            }
        )

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 20)
        ordered = list(reversed(self._events))
        return [copy.deepcopy(item) for item in ordered[:safe_limit]]

    def list_ticket_events(self, ticket_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        normalized_ticket_id = str(ticket_id).strip()
        filtered = [
            item
            for item in reversed(self._events)
            if str(item.get("ticket_id") or "").strip() == normalized_ticket_id
        ]
        return [copy.deepcopy(item) for item in filtered[:safe_limit]]

    def record_ticket_agent_event(
        self,
        ticket_id: str,
        message_id: str | None,
        run_id: str,
        agent_name: str,
        phase: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        self._agent_events.append(
            {
                "ticket_id": str(ticket_id).strip(),
                "message_id": str(message_id or "").strip() or None,
                "run_id": str(run_id).strip(),
                "agent_name": str(agent_name).strip(),
                "phase": str(phase).strip(),
                "event_type": str(event_type).strip(),
                "payload": copy.deepcopy(payload),
                "created_at": payload.get("created_at") or _utc_now(),
            }
        )

    def list_ticket_agent_events(self, ticket_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        normalized_ticket_id = str(ticket_id).strip()
        filtered = [
            item
            for item in reversed(self._agent_events)
            if str(item.get("ticket_id") or "").strip() == normalized_ticket_id
        ]
        return [copy.deepcopy(item) for item in filtered[:safe_limit]]

    def get_trace_ticket_snapshot(
        self,
        ticket_id: str,
        *,
        event_limit: int = 100,
        message_created_at: str | None = None,
        include_messages: bool = False,
        message_limit: int = 0,
    ) -> dict[str, Any] | None:
        ticket = self.get_ticket(ticket_id)
        if ticket is None:
            return None
        return _build_trace_ticket_snapshot_payload(
            ticket=ticket,
            ticket_events=self.list_ticket_events(ticket_id, limit=event_limit),
            agent_events=self.list_ticket_agent_events(ticket_id, limit=event_limit),
            message_created_at=message_created_at,
            include_messages=include_messages,
            message_limit=message_limit,
        )

    def save_billing_ticket(self, billing_ticket: dict[str, Any]) -> None:
        billing_ticket_id = str(billing_ticket.get("billing_ticket_id") or "").strip()
        if not billing_ticket_id:
            raise ValueError("billing_ticket_id is required")
        saved = copy.deepcopy(billing_ticket)
        saved.setdefault("created_at", _utc_now())
        saved.setdefault("updated_at", saved["created_at"])
        self._billing_tickets[billing_ticket_id] = saved

    def get_billing_ticket(self, billing_ticket_id: str) -> dict[str, Any] | None:
        ticket = self._billing_tickets.get(str(billing_ticket_id).strip())
        return copy.deepcopy(ticket) if ticket is not None else None

    def get_billing_ticket_by_client_ticket_id(self, client_ticket_id: str) -> dict[str, Any] | None:
        normalized_client_ticket_id = str(client_ticket_id).strip()
        if not normalized_client_ticket_id:
            return None
        for ticket in self._billing_tickets.values():
            if str(ticket.get("client_ticket_id") or "").strip() == normalized_client_ticket_id:
                return copy.deepcopy(ticket)
        return None

    def list_billing_tickets(
        self,
        limit: int = 30,
        review_status: str | None = None,
        offset: int = 0,
        automation_filter: str | None = None,
        route_errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 30)
        safe_offset = _safe_non_negative_int(offset, 0)
        items = sorted(
            self._billing_tickets.values(),
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )
        if review_status:
            items = [
                item
                for item in items
                if str(item.get("route_review_status") or "pending") == review_status
            ]
        if automation_filter == "automation":
            items = [
                item
                for item in items
                if str(item.get("automation_status") or item.get("status") or "").strip()
                in {"automation", "automated"}
            ]
        elif automation_filter == "not_automated":
            items = [
                item
                for item in items
                if str(item.get("automation_status") or item.get("status") or "").strip()
                not in {"automation", "automated"}
            ]
        if route_errors_only:
            corrected_ids = set(self._billing_route_corrections)
            items = [
                item
                for item in items
                if str(item.get("billing_ticket_id") or "").strip() in corrected_ids
                or _safe_float_value(item.get("route_confidence"), 1.0) < 0.6
            ]
        return [copy.deepcopy(item) for item in items[safe_offset : safe_offset + safe_limit]]

    def count_billing_tickets(
        self,
        review_status: str | None = None,
        automation_filter: str | None = None,
        route_errors_only: bool = False,
    ) -> int:
        return len(
            self.list_billing_tickets(
                limit=max(1, len(self._billing_tickets)),
                review_status=review_status,
                offset=0,
                automation_filter=automation_filter,
                route_errors_only=route_errors_only,
            )
        )

    def save_billing_response_token(self, token: dict[str, Any]) -> None:
        token_hash = str(token.get("token_hash") or "").strip()
        if not token_hash:
            raise ValueError("token_hash is required")
        billing_ticket_id = str(token.get("billing_ticket_id") or "").strip()
        if not billing_ticket_id:
            raise ValueError("billing_ticket_id is required")
        if token_hash in self._billing_response_tokens:
            return
        self._billing_response_tokens[token_hash] = copy.deepcopy(token)

    def get_billing_response_token(self, token_hash: str) -> dict[str, Any] | None:
        token = self._billing_response_tokens.get(str(token_hash).strip())
        return copy.deepcopy(token) if token is not None else None

    def mark_billing_response_token_used(self, token_hash: str, used_at: str) -> bool:
        token = self._billing_response_tokens.get(str(token_hash).strip())
        if token is None or token.get("used_at") is not None:
            return False
        token["used_at"] = used_at
        return True

    def save_billing_route_correction(self, correction: dict[str, Any]) -> None:
        billing_ticket_id = str(correction.get("billing_ticket_id") or "").strip()
        if not billing_ticket_id:
            raise ValueError("billing_ticket_id is required")
        saved = copy.deepcopy(correction)
        existing = self._billing_route_corrections.get(billing_ticket_id)
        if existing is not None:
            for key in (
                "original_scope_label",
                "original_route_family",
                "original_execution_action",
                "original_tooling_profile",
                "original_route_reason",
                "original_route_confidence",
                "first_corrected_scope_label",
                "first_corrected_route_family",
                "first_corrected_execution_action",
                "first_corrected_tooling_profile",
                "created_at",
            ):
                saved[key] = copy.deepcopy(existing.get(key))
            saved["correction_count"] = int(existing.get("correction_count") or 0) + 1
        else:
            saved["correction_count"] = 1
        saved.setdefault("created_at", _utc_now())
        saved.setdefault("updated_at", saved["created_at"])
        self._billing_route_corrections[billing_ticket_id] = saved

    def get_billing_route_correction(self, billing_ticket_id: str) -> dict[str, Any] | None:
        correction = self._billing_route_corrections.get(str(billing_ticket_id).strip())
        return copy.deepcopy(correction) if correction is not None else None

    def get_billing_route_corrections_for_tickets(
        self, billing_ticket_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for raw_id in billing_ticket_ids:
            normalized_id = str(raw_id or "").strip()
            if not normalized_id:
                continue
            correction = self._billing_route_corrections.get(normalized_id)
            if correction is not None:
                result[normalized_id] = copy.deepcopy(correction)
        return result

    def list_billing_route_corrections(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        items = sorted(
            self._billing_route_corrections.values(),
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )
        return [copy.deepcopy(item) for item in items[:safe_limit]]

    def apply_billing_route_correction(
        self,
        *,
        billing_ticket_id: str,
        active_route: dict[str, Any],
        correction: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_id = str(billing_ticket_id or "").strip()
        if not normalized_id:
            raise ValueError("billing_ticket_id is required")
        ticket = self._billing_tickets.get(normalized_id)
        if ticket is None:
            raise KeyError(normalized_id)
        existing = self._billing_route_corrections.get(normalized_id)
        saved = copy.deepcopy(correction)
        if existing is not None:
            for key in (
                "original_scope_label",
                "original_route_family",
                "original_execution_action",
                "original_tooling_profile",
                "original_route_reason",
                "original_route_confidence",
                "first_corrected_scope_label",
                "first_corrected_route_family",
                "first_corrected_execution_action",
                "first_corrected_tooling_profile",
                "created_at",
            ):
                saved[key] = copy.deepcopy(existing.get(key))
            saved["correction_count"] = int(existing.get("correction_count") or 0) + 1
        else:
            saved["correction_count"] = 1
            saved.setdefault("created_at", _utc_now())
        saved.setdefault("updated_at", saved.get("created_at") or _utc_now())
        saved["billing_ticket_id"] = normalized_id
        saved.setdefault("client_ticket_id", ticket.get("client_ticket_id"))

        updated_ticket = copy.deepcopy(ticket)
        for key, value in active_route.items():
            updated_ticket[key] = copy.deepcopy(value)
        updated_ticket["billing_ticket_id"] = normalized_id
        self._billing_tickets[normalized_id] = updated_ticket
        self._billing_route_corrections[normalized_id] = saved
        return copy.deepcopy(saved)

    def mark_billing_route_reviewed(
        self,
        *,
        billing_ticket_id: str,
        review_status: str,
    ) -> dict[str, Any]:
        normalized_id = str(billing_ticket_id or "").strip()
        if not normalized_id:
            raise ValueError("billing_ticket_id is required")
        ticket = self._billing_tickets.get(normalized_id)
        if ticket is None:
            raise KeyError(normalized_id)
        ticket["route_review_status"] = str(review_status or "pending").strip() or "pending"
        ticket["updated_at"] = _utc_now()
        return copy.deepcopy(ticket)


def _find_trace_customer_message_index(
    messages: list[dict[str, Any]],
    *,
    message_created_at: str | None,
) -> int | None:
    normalized_created_at = _clean_error_text(message_created_at)
    if normalized_created_at:
        for index, item in enumerate(messages):
            if _normalize_role(item.get("role")) != "customer":
                continue
            if _clean_error_text(item.get("created_at")) == normalized_created_at:
                return index
    for index in range(len(messages) - 1, -1, -1):
        if _normalize_role(messages[index].get("role")) == "customer":
            return index
    return None


def _find_trace_final_assistant_message(
    messages: list[dict[str, Any]],
    *,
    message_created_at: str | None,
) -> dict[str, Any] | None:
    customer_index = _find_trace_customer_message_index(
        messages,
        message_created_at=message_created_at,
    )
    if customer_index is None:
        return None
    final_assistant: dict[str, Any] | None = None
    for item in messages[customer_index + 1 :]:
        if _normalize_role(item.get("role")) != "assistant":
            continue
        if not _clean_error_text(item.get("content")):
            continue
        final_assistant = copy.deepcopy(item)
    return final_assistant


def _ticket_message_row_to_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": str(row[1]),
        "content": str(row[2]),
        "created_at": _to_iso(row[3]),
    }
    if row[4]:
        message["sentiment_label"] = str(row[4])
    if row[5]:
        message["sources"] = row[5]
    if row[6]:
        message["citations"] = row[6]
    if isinstance(row[7], dict):
        for key, value in row[7].items():
            normalized_key = str(key or "").strip()
            if not normalized_key or normalized_key in message:
                continue
            message[normalized_key] = value
    return message


def _limit_trace_messages(messages: list[dict[str, Any]], *, message_limit: int) -> list[dict[str, Any]]:
    if message_limit > 0:
        return [copy.deepcopy(item) for item in messages[-message_limit:]]
    return [copy.deepcopy(item) for item in messages]


def _build_trace_ticket_snapshot_payload(
    *,
    ticket: dict[str, Any],
    ticket_events: list[dict[str, Any]],
    agent_events: list[dict[str, Any]],
    message_created_at: str | None,
    include_messages: bool,
    message_limit: int,
    final_assistant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload_ticket = copy.deepcopy(ticket)
    ticket_messages = payload_ticket.get("messages") if isinstance(payload_ticket.get("messages"), list) else []
    resolved_final_assistant = (
        copy.deepcopy(final_assistant)
        if isinstance(final_assistant, dict)
        else _find_trace_final_assistant_message(
            ticket_messages,
            message_created_at=message_created_at,
        )
    )
    payload_ticket["messages"] = (
        _limit_trace_messages(ticket_messages, message_limit=message_limit)
        if include_messages
        else []
    )
    runtime_state = (
        copy.deepcopy(payload_ticket.get("client_agent_runtime_state"))
        if isinstance(payload_ticket.get("client_agent_runtime_state"), dict)
        else {}
    )
    return {
        "ticket": payload_ticket,
        "runtime_state": runtime_state,
        "final_assistant": resolved_final_assistant,
        "ticket_events": copy.deepcopy(ticket_events),
        "agent_events": copy.deepcopy(agent_events),
    }


class PostgresTicketRepository:
    def __init__(
        self,
        dsn: str,
        schema: str = "supportportal",
        connect_timeout: int = 10,
        connect_retries: int = 0,
        connect_retry_delay_seconds: float = 1.0,
        *,
        use_connection_pool: bool = False,
        pool_min_size: int = 1,
        pool_max_size: int = 8,
        pool_timeout_seconds: float = 5.0,
        pool_acquire_budget_seconds: float = 20.0,
        pool_max_lifetime_seconds: float = 300.0,
        pool_max_idle_seconds: float = 60.0,
        application_name: str | None = None,
        migration_dsn: str | None = None,
    ) -> None:
        self._dsn = dsn.strip()
        self._migration_dsn = str(migration_dsn or self._dsn).strip()
        self._schema = (schema or "supportportal").strip() or "supportportal"
        self._connect_timeout = _safe_positive_int(connect_timeout, 5)
        self._connect_retries = _safe_positive_int(connect_retries, 0)
        self._connect_retry_delay_seconds = _safe_positive_float(connect_retry_delay_seconds, 1.0)
        self._application_name = str(application_name or "").strip() or None
        self._use_connection_pool = bool(use_connection_pool)
        self._pool_min_size = _safe_positive_int(pool_min_size, 1)
        self._pool_max_size = max(self._pool_min_size, _safe_positive_int(pool_max_size, 8))
        requested_pool_timeout_seconds = _safe_positive_float(pool_timeout_seconds, 15.0)
        self._pool_timeout_seconds = max(requested_pool_timeout_seconds, float(self._connect_timeout))
        self._pool_acquire_budget_seconds = _safe_positive_float(pool_acquire_budget_seconds, 20.0)
        self._pool_max_lifetime_seconds = _safe_positive_float(pool_max_lifetime_seconds, 1800.0)
        self._pool_max_idle_seconds = _safe_positive_float(pool_max_idle_seconds, 300.0)
        if self._use_connection_pool and ConnectionPool is None:
            raise RuntimeError("psycopg_pool is required when TICKET_DB connection pooling is enabled")
        self._pool: Any = None
        self._pool_lock = threading.Lock()

    def storage_mode(self) -> str:
        return "postgres"

    def _table(self, table_name: str) -> sql.Identifier:
        return sql.Identifier(self._schema, table_name)

    def _connect(self) -> psycopg.Connection[Any]:
        return self._connect_dsn(self._dsn)

    def _connect_dsn(self, dsn: str) -> psycopg.Connection[Any]:
        attempts = max(1, self._connect_retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                connect_kwargs: dict[str, Any] = {"connect_timeout": self._connect_timeout}
                if self._application_name:
                    connect_kwargs["application_name"] = self._application_name
                connection = psycopg.connect(dsn, **connect_kwargs)
                connection.autocommit = True
                return connection
            except (psycopg.OperationalError, psycopg.Error, OSError, TimeoutError) as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                LOGGER.warning(
                    "Ticket repository connection failed attempt %s/%s: %s",
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(self._connect_retry_delay_seconds)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Ticket repository connection failed without an exception")

    def _connect_for_initialize(self) -> psycopg.Connection[Any]:
        return self._connect_dsn(self._migration_dsn)

    def _runtime_database_role(self) -> str | None:
        if self._dsn == self._migration_dsn:
            return None
        with self._connect_dsn(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_user")
                row = cur.fetchone()
        role = str(row[0] if row else "").strip()
        if not role:
            raise RuntimeError("Unable to resolve the ticket database runtime role")
        return role

    def _grant_runtime_privileges(self, cur: psycopg.Cursor[Any], runtime_role: str) -> None:
        schema = sql.Identifier(self._schema)
        role = sql.Identifier(runtime_role)
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role))
        cur.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}"
            ).format(schema, role)
        )
        cur.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                schema, role
            )
        )
        cur.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA {} "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(schema, role)
        )
        cur.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA {} "
                "GRANT USAGE, SELECT ON SEQUENCES TO {}"
            ).format(schema, role)
        )

    def _pool_factory(self) -> Any:
        if ConnectionPool is None:
            raise RuntimeError("psycopg_pool is required when TICKET_DB connection pooling is enabled")
        pool_kwargs: dict[str, Any] = {
            "connect_timeout": self._connect_timeout,
            "autocommit": True,
        }
        if self._application_name:
            pool_kwargs["application_name"] = self._application_name
        return ConnectionPool(
            self._dsn,
            kwargs=pool_kwargs,
            min_size=self._pool_min_size,
            max_size=self._pool_max_size,
            check=ConnectionPool.check_connection,
            timeout=self._pool_timeout_seconds,
            max_lifetime=self._pool_max_lifetime_seconds,
            max_idle=self._pool_max_idle_seconds,
            open=False,
        )

    def _close_pool_instance(self, pool: Any) -> None:
        if pool is None:
            return
        try:
            close = getattr(pool, "close", None)
            if callable(close):
                close()
        except Exception:
            LOGGER.debug("Failed to close ticket repository connection pool cleanly.", exc_info=True)

    def close(self) -> None:
        with self._pool_lock:
            pool = self._pool
            self._pool = None
        self._close_pool_instance(pool)

    def _classify_pool_timeout(
        self,
        exc: Exception,
        *,
        phase: str,
        pool: Any | None = None,
        timeout_seconds: float | None = None,
    ) -> psycopg.OperationalError:
        message = _clean_error_text(exc)
        lowered = message.lower()
        effective_timeout_seconds = _safe_positive_float(timeout_seconds, self._pool_timeout_seconds)
        if phase == "open":
            if "connection timeout expired" in lowered:
                detail = (
                    f"ticket db pool warm-up failed because backend connection establishment timed out "
                    f"after {effective_timeout_seconds:.2f} sec"
                )
            else:
                detail = (
                    f"ticket db pool warm-up did not complete after {effective_timeout_seconds:.2f} sec"
                )
        else:
            pool_stats: dict[str, Any] = {}
            try:
                get_stats = getattr(pool, "get_stats", None)
                if callable(get_stats):
                    stats_value = get_stats()
                    if isinstance(stats_value, dict):
                        pool_stats = stats_value
            except Exception:
                pool_stats = {}
            pool_available = _safe_non_negative_int(pool_stats.get("pool_available"), 0)
            requests_waiting = _safe_non_negative_int(pool_stats.get("requests_waiting"), 0)
            pool_size = _safe_non_negative_int(pool_stats.get("pool_size"), self._pool_max_size)
            if "connection timeout expired" in lowered:
                detail = (
                    f"ticket db pool borrow timed out after {effective_timeout_seconds:.2f} sec "
                    f"while backend connections were still establishing"
                )
            elif pool_available <= 0 and requests_waiting > 0 and pool_size >= self._pool_max_size:
                detail = (
                    f"ticket db pool borrow timed out after {effective_timeout_seconds:.2f} sec "
                    f"because the pool was fully leased"
                )
            else:
                detail = (
                    f"ticket db pool borrow timed out after {effective_timeout_seconds:.2f} sec"
                )
            stats_parts: list[str] = []
            for key in ("pool_available", "requests_waiting", "pool_size"):
                if key in pool_stats:
                    stats_parts.append(f"{key}={_safe_non_negative_int(pool_stats.get(key), 0)}")
            if stats_parts:
                detail = f"{detail} ({', '.join(stats_parts)})"
        if message:
            detail = f"{detail}: {message}"
        return psycopg.OperationalError(detail)

    def _classify_pool_acquire_budget_exhausted(self) -> psycopg.OperationalError:
        return psycopg.OperationalError(
            f"ticket db pool acquire budget exhausted after {self._pool_acquire_budget_seconds:.2f} sec"
        )

    def _pool_acquire_deadline(self) -> float | None:
        if not self._use_connection_pool:
            return None
        return time.monotonic() + self._pool_acquire_budget_seconds

    def _remaining_pool_acquire_timeout(self, acquire_deadline: float | None) -> float:
        if acquire_deadline is None:
            return self._pool_timeout_seconds
        remaining_timeout_seconds = acquire_deadline - time.monotonic()
        if remaining_timeout_seconds <= 0:
            raise self._classify_pool_acquire_budget_exhausted()
        return min(self._pool_timeout_seconds, remaining_timeout_seconds)

    def _sleep_pool_connect_retry(self, acquire_deadline: float | None) -> None:
        if acquire_deadline is None:
            time.sleep(self._connect_retry_delay_seconds)
            return
        remaining_timeout_seconds = acquire_deadline - time.monotonic()
        if remaining_timeout_seconds <= 0:
            raise self._classify_pool_acquire_budget_exhausted()
        time.sleep(min(self._connect_retry_delay_seconds, remaining_timeout_seconds))

    def _connection_pool(self, *, acquire_deadline: float | None = None) -> Any:
        if not self._use_connection_pool:
            return None
        existing_pool = self._pool
        if existing_pool is not None and not bool(getattr(existing_pool, "closed", False)):
            return self._pool
        with self._pool_lock:
            existing_pool = self._pool
            if existing_pool is not None and not bool(getattr(existing_pool, "closed", False)):
                return existing_pool
            self._pool = None
            attempts = max(1, self._connect_retries + 1)
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                pool = self._pool_factory()
                try:
                    pool.open(wait=False)
                    self._pool = pool
                    return pool
                except Exception as exc:
                    self._close_pool_instance(pool)
                    last_error = exc
                    if attempt >= attempts:
                        raise self._classify_pool_timeout(exc, phase="open") from exc
                    LOGGER.warning(
                        "Ticket repository pool warm-up failed attempt %s/%s: %s",
                        attempt,
                        attempts,
                        exc,
                    )
                    self._sleep_pool_connect_retry(acquire_deadline)
            if last_error is not None:
                raise self._classify_pool_timeout(last_error, phase="open") from last_error
        return self._pool

    @contextmanager
    def _borrow_connection(self, *, acquire_deadline: float | None = None) -> Any:
        pool = self._connection_pool(acquire_deadline=acquire_deadline)
        if pool is None:
            connection = self._connect()
            try:
                yield connection
            finally:
                if not getattr(connection, "closed", False):
                    try:
                        connection.close()
                    except Exception:
                        LOGGER.debug("Failed to close direct ticket repository connection cleanly.", exc_info=True)
            return
        timeout_seconds = self._remaining_pool_acquire_timeout(acquire_deadline)
        try:
            with pool.connection(timeout=timeout_seconds) as connection:
                yield connection
        except Exception as exc:
            if PoolTimeout is not None and isinstance(exc, PoolTimeout):
                raise self._classify_pool_timeout(
                    exc,
                    phase="borrow",
                    pool=pool,
                    timeout_seconds=timeout_seconds,
                ) from exc
            raise

    def _invalidate_connection(self, conn: psycopg.Connection[Any]) -> None:
        try:
            conn.close()
        except Exception:
            LOGGER.debug("Failed to invalidate ticket repository connection cleanly.", exc_info=True)

    def _should_retry_connection_error(
        self,
        conn: psycopg.Connection[Any],
        exc: Exception,
    ) -> bool:
        if getattr(conn, "closed", False) or getattr(conn, "broken", False):
            return True
        return _is_retryable_storage_error(exc)

    def _should_retry_pool_acquire_error(self, exc: Exception) -> bool:
        if not self._use_connection_pool:
            return False
        if not isinstance(exc, (psycopg.OperationalError, psycopg.Error, OSError, TimeoutError)):
            return False
        lowered = _clean_error_text(exc).lower()
        return (
            "ticket db pool warm-up failed" in lowered
            or "ticket db pool warm-up did not complete" in lowered
            or "ticket db pool borrow timed out" in lowered
            or "while backend connections were still establishing" in lowered
            or "connection timeout expired" in lowered
        )

    def _run_with_connection_retry(
        self,
        operation_name: str,
        action: Callable[[psycopg.Connection[Any]], _ResultT],
    ) -> _ResultT:
        attempt = 0
        pool_retry_attempt = 0
        acquire_deadline = self._pool_acquire_deadline()
        while True:
            try:
                with self._borrow_connection(acquire_deadline=acquire_deadline) as conn:
                    try:
                        return action(conn)
                    except Exception as exc:
                        should_retry = self._should_retry_connection_error(conn, exc)
                        if should_retry:
                            self._invalidate_connection(conn)
                        if not should_retry or attempt >= 1:
                            raise
                        attempt += 1
                        LOGGER.warning(
                            "Ticket repository %s hit a retryable storage error; resetting connection and retrying once: %s",
                            operation_name,
                            exc,
                        )
            except Exception as exc:
                should_retry_pool = self._should_retry_pool_acquire_error(exc)
                if not should_retry_pool or pool_retry_attempt >= 1:
                    raise
                pool_retry_attempt += 1
                LOGGER.warning(
                    "Ticket repository %s hit a retryable pool acquire error; rebuilding the pool and retrying once: %s",
                    operation_name,
                    exc,
                )
                self.close()

    def initialize(self) -> None:
        runtime_role = self._runtime_database_role()
        with self._connect_for_initialize() as conn:
            # Keep the transaction-scoped advisory lock for the entire schema bootstrap.
            conn.autocommit = False
            with conn.cursor() as cur:
                # Serialize bootstrap across services/workers sharing the same AWS database.
                cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (842918, 1))
                cur.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        sql.Identifier(self._schema)
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            config_key TEXT PRIMARY KEY,
                            config_value TEXT NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(self._table("support_ticket_schema_meta"))
                )
                cur.execute(
                    sql.SQL("SELECT config_value FROM {} WHERE config_key = %s").format(
                        self._table("support_ticket_schema_meta")
                    ),
                    (_TICKET_SCHEMA_VERSION_KEY,),
                )
                version_row = cur.fetchone()
                existing_version = str(version_row[0]).strip() if version_row else ""
                if existing_version and existing_version not in _COMPATIBLE_INCREMENTAL_SCHEMA_VERSIONS:
                    for table_name in (
                        "support_ticket_investigation_messages",
                        "support_ticket_investigations",
                        "support_ticket_events",
                        "support_ticket_messages",
                        "support_tickets",
                    ):
                        cur.execute(
                            sql.SQL("DROP TABLE IF EXISTS {}").format(self._table(table_name))
                        )

                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            ticket_id TEXT PRIMARY KEY,
                            customer_id TEXT NOT NULL,
                            requester TEXT NOT NULL,
                            subject TEXT NOT NULL,
                            status TEXT NOT NULL,
                            last_engineer_action JSONB,
                            active_engineer_case_id TEXT,
                            engineer_case_count INTEGER NOT NULL DEFAULT 0,
                            product TEXT,
                            product_selection_state JSONB,
                            client_intake_state JSONB,
                            client_agent_runtime_state JSONB,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(self._table("support_tickets"))
                )
                cur.execute(
                    sql.SQL(
                        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS active_engineer_case_id TEXT"
                    ).format(self._table("support_tickets"))
                )
                cur.execute(
                    sql.SQL(
                        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS engineer_case_count INTEGER NOT NULL DEFAULT 0"
                    ).format(self._table("support_tickets"))
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS product TEXT").format(
                        self._table("support_tickets")
                    )
                )
                cur.execute(
                    sql.SQL(
                        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS product_selection_state JSONB"
                    ).format(self._table("support_tickets"))
                )
                cur.execute(
                    sql.SQL(
                        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS client_intake_state JSONB"
                    ).format(self._table("support_tickets"))
                )
                cur.execute(
                    sql.SQL(
                        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS client_agent_runtime_state JSONB"
                    ).format(self._table("support_tickets"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            role TEXT NOT NULL,
                            content TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            sentiment_label TEXT,
                            sources JSONB,
                            citations JSONB,
                            meta JSONB NOT NULL DEFAULT '{{}}'::jsonb
                        )
                        """
                    ).format(
                        self._table("support_ticket_messages"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{{}}'::jsonb"
                    ).format(self._table("support_ticket_messages"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            message_id TEXT,
                            run_id TEXT NOT NULL,
                            agent_name TEXT NOT NULL,
                            phase TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            payload JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_ticket_agent_events"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            ticket_id TEXT REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            event_type TEXT NOT NULL,
                            payload JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_ticket_events"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            investigation_id TEXT PRIMARY KEY,
                            ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            state TEXT NOT NULL,
                            trigger_reason TEXT NOT NULL,
                            trigger_source TEXT NOT NULL,
                            draft_customer_reply TEXT,
                            final_confirmation_requested_at TIMESTAMPTZ,
                            opened_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL,
                            closed_at TIMESTAMPTZ
                        )
                        """
                    ).format(
                        self._table("support_ticket_investigations"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            message_id TEXT NOT NULL,
                            investigation_id TEXT NOT NULL REFERENCES {}(investigation_id) ON DELETE CASCADE,
                            role TEXT NOT NULL,
                            content TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            meta JSONB
                        )
                        """
                    ).format(
                        self._table("support_ticket_investigation_messages"),
                        self._table("support_ticket_investigations"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            engineer_case_id TEXT PRIMARY KEY,
                            client_ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            case_sequence INTEGER NOT NULL,
                            title TEXT NOT NULL,
                            status TEXT NOT NULL,
                            trigger_source TEXT NOT NULL,
                            trigger_reason TEXT NOT NULL,
                            draft_customer_reply TEXT,
                            final_confirmation_requested_at TIMESTAMPTZ,
                            engineer_handoff_packet JSONB,
                            engineer_agent_state JSONB,
                            opened_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL,
                            closed_at TIMESTAMPTZ,
                            assigned_engineer_id TEXT,
                            assignment_status TEXT NOT NULL DEFAULT 'pending',
                            assigned_at TIMESTAMPTZ,
                            sla_due_at TIMESTAMPTZ,
                            assignment_attempt_count INTEGER NOT NULL DEFAULT 0,
                            previous_assignees JSONB NOT NULL DEFAULT '[]'::jsonb,
                            last_assignment_reason TEXT,
                            dispatch_status TEXT NOT NULL DEFAULT 'pending',
                            assignment_updated_at TIMESTAMPTZ,
                            assignment_version INTEGER NOT NULL DEFAULT 0
                        )
                        """
                    ).format(
                        self._table("support_engineer_cases"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS assigned_engineer_id TEXT").format(
                        self._table("support_engineer_cases")
                    )
                )
                engineer_case_assignment_columns = (
                    "assignment_status TEXT NOT NULL DEFAULT 'pending'",
                    "assigned_at TIMESTAMPTZ",
                    "sla_due_at TIMESTAMPTZ",
                    "assignment_attempt_count INTEGER NOT NULL DEFAULT 0",
                    "previous_assignees JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "last_assignment_reason TEXT",
                    "dispatch_status TEXT NOT NULL DEFAULT 'pending'",
                    "assignment_updated_at TIMESTAMPTZ",
                    "assignment_version INTEGER NOT NULL DEFAULT 0",
                )
                for column_definition in engineer_case_assignment_columns:
                    cur.execute(
                        sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {}").format(
                            self._table("support_engineer_cases"),
                            sql.SQL(column_definition),
                        )
                    )
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET assignment_status = CASE
                                WHEN closed_at IS NOT NULL OR status = 'resolved' THEN 'resolved'
                                WHEN assigned_engineer_id IS NOT NULL THEN 'assigned'
                                ELSE 'pending'
                            END,
                            dispatch_status = CASE
                                WHEN closed_at IS NOT NULL OR status = 'resolved' THEN 'resolved'
                                WHEN assigned_engineer_id IS NOT NULL THEN 'assigned'
                                ELSE 'pending'
                            END
                        WHERE assignment_version = 0
                        """
                    ).format(self._table("support_engineer_cases"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            account_id TEXT PRIMARY KEY,
                            display_name TEXT NOT NULL,
                            role TEXT NOT NULL,
                            password_hash TEXT NOT NULL,
                            active BOOLEAN NOT NULL DEFAULT TRUE,
                            availability TEXT NOT NULL DEFAULT 'unavailable',
                            availability_reason TEXT,
                            availability_updated_at TIMESTAMPTZ,
                            last_assigned_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(self._table("support_workspace_accounts"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            event_type TEXT NOT NULL,
                            actor_id TEXT NOT NULL,
                            target_id TEXT,
                            payload JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(self._table("support_workspace_audit_events"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            scope TEXT NOT NULL,
                            idempotency_key TEXT NOT NULL,
                            state TEXT NOT NULL,
                            response_payload JSONB,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL,
                            PRIMARY KEY (scope, idempotency_key)
                        )
                        """
                    ).format(self._table("support_idempotency_records"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            counter_key TEXT PRIMARY KEY,
                            current_value BIGINT NOT NULL DEFAULT 0,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(self._table("support_rollout_counters"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            counter_key TEXT NOT NULL REFERENCES {}(counter_key) ON DELETE CASCADE,
                            event_key TEXT NOT NULL,
                            position BIGINT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            PRIMARY KEY (counter_key, event_key),
                            UNIQUE (counter_key, position)
                        )
                        """
                    ).format(
                        self._table("support_rollout_events"),
                        self._table("support_rollout_counters"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            message_id TEXT NOT NULL,
                            engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                            role TEXT NOT NULL,
                            content TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            meta JSONB
                        )
                        """
                    ).format(
                        self._table("support_engineer_case_messages"),
                        self._table("support_engineer_cases"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            engineer_case_id TEXT REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                            event_type TEXT NOT NULL,
                            payload JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_engineer_case_events"),
                        self._table("support_engineer_cases"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            feedback_id TEXT PRIMARY KEY,
                            engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                            client_ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            run_id TEXT,
                            message_id TEXT,
                            evidence_packet_id TEXT,
                            feedback_type TEXT NOT NULL,
                            diagnosis_correctness TEXT NOT NULL,
                            root_cause_correctness TEXT NOT NULL,
                            evidence_quality TEXT NOT NULL,
                            citation_quality TEXT NOT NULL,
                            customer_reply_quality TEXT NOT NULL,
                            missing_information JSONB NOT NULL DEFAULT '[]'::jsonb,
                            incorrect_claims JSONB NOT NULL DEFAULT '[]'::jsonb,
                            corrected_root_cause TEXT,
                            corrected_solution TEXT,
                            corrected_customer_reply TEXT,
                            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                            memory_candidate TEXT NOT NULL,
                            memory_safety TEXT NOT NULL,
                            memory_notes TEXT,
                            prompt_version TEXT,
                            workflow_version TEXT,
                            tool_policy_version TEXT,
                            rag_access_policy_version TEXT,
                            evidence_packet_version TEXT,
                            created_by TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_engineer_hitl_feedback"),
                        self._table("support_engineer_cases"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            memory_record_id TEXT PRIMARY KEY,
                            source_feedback_id TEXT NOT NULL REFERENCES {}(feedback_id) ON DELETE CASCADE,
                            engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                            client_ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            feedback_type TEXT NOT NULL,
                            ledger_status TEXT NOT NULL,
                            retrieval_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                            active_memory_status TEXT NOT NULL,
                            symptom TEXT,
                            root_cause TEXT,
                            solution TEXT,
                            customer_safe_summary TEXT,
                            internal_only_summary TEXT,
                            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                            safety_label TEXT NOT NULL,
                            quality_label TEXT NOT NULL,
                            memory_schema_version TEXT NOT NULL,
                            prompt_version TEXT,
                            workflow_version TEXT,
                            tool_policy_version TEXT,
                            rag_access_policy_version TEXT,
                            evidence_packet_version TEXT,
                            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_case_memory_ledger"),
                        self._table("support_engineer_hitl_feedback"),
                        self._table("support_engineer_cases"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            eval_item_id TEXT PRIMARY KEY,
                            client_ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                            source_summary_packet_id TEXT NOT NULL DEFAULT '',
                            source_summary_packet_version TEXT NOT NULL DEFAULT '',
                            source_plan_id TEXT NOT NULL DEFAULT '',
                            source_execution_id TEXT NOT NULL DEFAULT '',
                            source_review_id TEXT NOT NULL DEFAULT '',
                            review_decision TEXT NOT NULL DEFAULT '',
                            review_trace JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            replan_notes JSONB NOT NULL DEFAULT '[]'::jsonb,
                            engineer_revise_feedback JSONB NOT NULL DEFAULT '[]'::jsonb,
                            approved_reply TEXT NOT NULL DEFAULT '',
                            guardrail_final JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            expected_outcome TEXT NOT NULL DEFAULT 'resolved_with_customer_reply',
                            replay_input JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            reference_output JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            dataset_status TEXT NOT NULL DEFAULT 'candidate',
                            schema_version TEXT NOT NULL DEFAULT '',
                            data_quality_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_engineer_replay_eval_items"),
                        self._table("support_tickets"),
                        self._table("support_engineer_cases"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (config_key, config_value, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (config_key) DO UPDATE SET
                            config_value = EXCLUDED.config_value,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(self._table("support_ticket_schema_meta")),
                    (_TICKET_SCHEMA_VERSION_KEY, _TICKET_SCHEMA_VERSION),
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, updated_at DESC)").format(
                        sql.Identifier("idx_support_tickets_status_updated"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (ticket_id, created_at ASC, id ASC)"
                    ).format(
                        sql.Identifier("idx_support_ticket_messages_ticket_created"),
                        self._table("support_ticket_messages"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (ticket_id, created_at DESC)").format(
                        sql.Identifier("idx_support_ticket_events_ticket_created"),
                        self._table("support_ticket_events"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (ticket_id, created_at DESC)").format(
                        sql.Identifier("idx_support_ticket_agent_events_ticket_created"),
                        self._table("support_ticket_agent_events"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (ticket_id, updated_at DESC)").format(
                        sql.Identifier("idx_support_ticket_investigations_ticket_updated"),
                        self._table("support_ticket_investigations"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (investigation_id, created_at ASC, id ASC)"
                    ).format(
                        sql.Identifier("idx_support_ticket_investigation_messages_created"),
                        self._table("support_ticket_investigation_messages"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (client_ticket_id, updated_at DESC)").format(
                        sql.Identifier("idx_support_engineer_cases_ticket_updated"),
                        self._table("support_engineer_cases"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, updated_at DESC)").format(
                        sql.Identifier("idx_support_engineer_cases_status_updated"),
                        self._table("support_engineer_cases"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (engineer_case_id, created_at ASC, id ASC)").format(
                        sql.Identifier("idx_support_engineer_case_messages_created"),
                        self._table("support_engineer_case_messages"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (engineer_case_id, created_at DESC)").format(
                        sql.Identifier("idx_support_engineer_case_events_created"),
                        self._table("support_engineer_case_events"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (engineer_case_id, created_at DESC)").format(
                        sql.Identifier("idx_support_engineer_hitl_feedback_case_created"),
                        self._table("support_engineer_hitl_feedback"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (client_ticket_id, created_at DESC)").format(
                        sql.Identifier("idx_support_engineer_hitl_feedback_ticket_created"),
                        self._table("support_engineer_hitl_feedback"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (engineer_case_id, created_at DESC)").format(
                        sql.Identifier("idx_support_case_memory_ledger_case_created"),
                        self._table("support_case_memory_ledger"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (client_ticket_id, created_at DESC)").format(
                        sql.Identifier("idx_support_case_memory_ledger_ticket_created"),
                        self._table("support_case_memory_ledger"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (retrieval_enabled, ledger_status, updated_at DESC)"
                    ).format(
                        sql.Identifier("idx_support_case_memory_ledger_retrieval"),
                        self._table("support_case_memory_ledger"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (engineer_case_id, created_at DESC)").format(
                        sql.Identifier("idx_support_engineer_replay_eval_case_created"),
                        self._table("support_engineer_replay_eval_items"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (client_ticket_id, created_at DESC)").format(
                        sql.Identifier("idx_support_engineer_replay_eval_ticket_created"),
                        self._table("support_engineer_replay_eval_items"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (dataset_status, created_at DESC)").format(
                        sql.Identifier("idx_support_engineer_replay_eval_status_created"),
                        self._table("support_engineer_replay_eval_items"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            billing_ticket_id TEXT PRIMARY KEY,
                            client_ticket_id TEXT NOT NULL UNIQUE REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            source TEXT NOT NULL,
                            external_id TEXT,
                            created_by TEXT,
                            title TEXT NOT NULL,
                            question TEXT NOT NULL,
                            route TEXT,
                            scope_label TEXT,
                            route_family TEXT,
                            execution_action TEXT,
                            tooling_profile TEXT,
                            route_reason TEXT,
                            route_confidence REAL,
                            matched_signals JSONB,
                            automation_status TEXT NOT NULL,
                            missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
                            collected_fields JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            customer_reply TEXT,
                            internal_email_payload JSONB,
                            internal_email_send_status TEXT,
                            internal_email_send_reason TEXT,
                            semantic_intent TEXT,
                            automation_eligibility TEXT,
                            policy_decision TEXT,
                            not_automated_reason TEXT,
                            risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
                            evidence_spans JSONB NOT NULL DEFAULT '[]'::jsonb,
                            router_source TEXT,
                            route_review_status TEXT NOT NULL DEFAULT 'pending',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_billing_tickets"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS scope_label TEXT").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS route_family TEXT").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS execution_action TEXT").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS tooling_profile TEXT").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS semantic_intent TEXT").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS automation_eligibility TEXT").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS policy_decision TEXT").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS not_automated_reason TEXT").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_spans JSONB NOT NULL DEFAULT '[]'::jsonb").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS router_source TEXT").format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS route_review_status TEXT NOT NULL DEFAULT 'pending'"
                    ).format(
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            billing_ticket_id TEXT PRIMARY KEY REFERENCES {}(billing_ticket_id) ON DELETE CASCADE,
                            client_ticket_id TEXT NOT NULL,
                            original_scope_label TEXT,
                            original_route_family TEXT,
                            original_execution_action TEXT,
                            original_tooling_profile TEXT,
                            original_route_reason TEXT,
                            original_route_confidence REAL,
                            corrected_scope_label TEXT NOT NULL,
                            corrected_route_family TEXT NOT NULL,
                            corrected_execution_action TEXT NOT NULL,
                            corrected_tooling_profile TEXT NOT NULL,
                            first_corrected_scope_label TEXT NOT NULL,
                            first_corrected_route_family TEXT NOT NULL,
                            first_corrected_execution_action TEXT NOT NULL,
                            first_corrected_tooling_profile TEXT NOT NULL,
                            corrector TEXT,
                            correction_count INTEGER NOT NULL DEFAULT 1,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_billing_route_corrections"),
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (updated_at DESC)").format(
                        sql.Identifier("idx_support_billing_route_corrections_updated"),
                        self._table("support_billing_route_corrections"),
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} DROP COLUMN IF EXISTS note").format(
                        self._table("support_billing_route_corrections"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (created_at DESC)"
                    ).format(
                        sql.Identifier("idx_support_billing_tickets_created"),
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            token_hash TEXT PRIMARY KEY,
                            billing_ticket_id TEXT NOT NULL REFERENCES {}(billing_ticket_id) ON DELETE CASCADE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            used_at TIMESTAMPTZ
                        )
                        """
                    ).format(
                        self._table("support_billing_response_tokens"),
                        self._table("support_billing_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (billing_ticket_id, created_at DESC)").format(
                        sql.Identifier("idx_support_billing_response_tokens_ticket"),
                        self._table("support_billing_response_tokens"),
                    )
                )
                self._backfill_engineer_cases_from_legacy_storage(cur)
                if runtime_role:
                    self._grant_runtime_privileges(cur, runtime_role)
            conn.commit()

    def _legacy_support_ticket_has_column(self, cur: psycopg.Cursor[Any], column_name: str) -> bool:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = 'support_tickets'
              AND column_name = %s
            LIMIT 1
            """,
            (self._schema, column_name),
        )
        return cur.fetchone() is not None

    def _backfill_engineer_case_title(
        self,
        *,
        subject: str,
        messages: list[dict[str, Any]],
    ) -> str:
        for message in reversed(messages):
            if _normalize_role(message.get("role")) != "customer":
                continue
            content = str(message.get("content") or "").strip()
            if content:
                return content[:120]
        return subject[:120] or "Engineer case"

    def _backfill_engineer_cases_from_legacy_storage(self, cur: psycopg.Cursor[Any]) -> None:
        legacy_handoff_supported = self._legacy_support_ticket_has_column(cur, "engineer_handoff_packet")
        legacy_agent_supported = self._legacy_support_ticket_has_column(cur, "engineer_agent_state")

        select_fields = [
            sql.SQL("ticket_id"),
            sql.SQL("subject"),
            sql.SQL("status"),
        ]
        if legacy_handoff_supported:
            select_fields.append(sql.SQL("engineer_handoff_packet"))
        else:
            select_fields.append(sql.SQL("NULL::jsonb"))
        if legacy_agent_supported:
            select_fields.append(sql.SQL("engineer_agent_state"))
        else:
            select_fields.append(sql.SQL("NULL::jsonb"))

        cur.execute(
            sql.SQL("SELECT {} FROM {}").format(
                sql.SQL(", ").join(select_fields),
                self._table("support_tickets"),
            )
        )
        rows = cur.fetchall()
        ticket_ids = [str(row[0]) for row in rows]
        if not ticket_ids:
            return

        connection = cur.connection
        message_map = self._fetch_messages(connection, ticket_ids)
        investigation_map = self._fetch_investigations(
            connection,
            ticket_ids,
            include_messages=True,
        )
        cur.execute(
            sql.SQL(
                """
                SELECT client_ticket_id, COUNT(*)
                FROM {}
                GROUP BY client_ticket_id
                """
            ).format(self._table("support_engineer_cases"))
        )
        existing_counts = {
            str(row[0]): _safe_non_negative_int(row[1], 0)
            for row in cur.fetchall()
        }

        for row in rows:
            ticket_id = str(row[0])
            subject = str(row[1] or "").strip()
            ticket_status = _normalize_status(row[2])
            legacy_handoff = row[3] if isinstance(row[3], dict) else None
            legacy_agent = row[4] if isinstance(row[4], dict) else None
            if existing_counts.get(ticket_id, 0):
                continue
            investigations = sorted(
                investigation_map.get(ticket_id, []),
                key=lambda item: str(item.get("opened_at") or item.get("updated_at") or ""),
            )
            if not investigations:
                continue
            active_case_id: str | None = None
            for index, investigation in enumerate(investigations, start=1):
                engineer_case_id = f"{ticket_id}-{index}"
                is_closed = _normalize_investigation_state(investigation.get("state")) == "closed"
                case_status = ticket_status if is_closed else "investigating"
                case_title = self._backfill_engineer_case_title(
                    subject=subject,
                    messages=message_map.get(ticket_id, []),
                )
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            engineer_case_id,
                            client_ticket_id,
                            case_sequence,
                            title,
                            status,
                            trigger_source,
                            trigger_reason,
                            draft_customer_reply,
                            final_confirmation_requested_at,
                            engineer_handoff_packet,
                            engineer_agent_state,
                            opened_at,
                            updated_at,
                            closed_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (engineer_case_id) DO NOTHING
                        """
                    ).format(self._table("support_engineer_cases")),
                    (
                        engineer_case_id,
                        ticket_id,
                        index,
                        case_title,
                        case_status,
                        str(investigation.get("trigger_source") or "legacy_backfill"),
                        str(investigation.get("trigger_reason") or "legacy_backfill"),
                        str(investigation.get("draft_customer_reply") or ""),
                        investigation.get("final_confirmation_requested_at"),
                        Json(legacy_handoff) if legacy_handoff else None,
                        Json(legacy_agent) if legacy_agent else None,
                        investigation.get("opened_at"),
                        investigation.get("updated_at"),
                        investigation.get("closed_at"),
                    ),
                )
                for message in list(investigation.get("messages") or []):
                    if not isinstance(message, dict):
                        continue
                    content = str(message.get("content") or "").strip()
                    if not content:
                        continue
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                message_id,
                                engineer_case_id,
                                role,
                                content,
                                created_at,
                                meta
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING
                            """
                        ).format(self._table("support_engineer_case_messages")),
                        (
                            str(message.get("id") or f"{engineer_case_id}-{uuid4().hex[:8]}"),
                            engineer_case_id,
                            _normalize_investigation_role(message.get("role")),
                            content,
                            message.get("created_at") or investigation.get("updated_at") or _utc_now(),
                            Json(message.get("meta")) if isinstance(message.get("meta"), dict) else None,
                        ),
                    )
                if not is_closed:
                    active_case_id = engineer_case_id
            cur.execute(
                sql.SQL(
                    """
                    UPDATE {}
                    SET active_engineer_case_id = %s,
                        engineer_case_count = %s
                    WHERE ticket_id = %s
                    """
                ).format(self._table("support_tickets")),
                (
                    active_case_id,
                    len(investigations),
                    ticket_id,
                ),
            )

    def exists(self, ticket_id: str) -> bool:
        def _operation(conn: psycopg.Connection[Any]) -> bool:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE ticket_id = %s").format(
                        self._table("support_tickets")
                    ),
                    (ticket_id,),
                )
                return cur.fetchone() is not None

        return self._run_with_connection_retry("exists", _operation)

    def _fetch_messages(self, conn: psycopg.Connection[Any], ticket_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {ticket_id: [] for ticket_id in ticket_ids}
        if not ticket_ids:
            return grouped
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT ticket_id, role, content, created_at, sentiment_label, sources, citations, meta
                    FROM {}
                    WHERE ticket_id = ANY(%s)
                    ORDER BY created_at ASC, id ASC
                    """
                ).format(self._table("support_ticket_messages")),
                (ticket_ids,),
            )
            for row in cur.fetchall():
                grouped[str(row[0])].append(_ticket_message_row_to_payload(row))
        return grouped

    def _fetch_recent_messages_for_trace(
        self,
        conn: psycopg.Connection[Any],
        *,
        ticket_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 1)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT ticket_id, role, content, created_at, sentiment_label, sources, citations, meta
                    FROM (
                        SELECT ticket_id, role, content, created_at, sentiment_label, sources, citations, meta, id
                        FROM {}
                        WHERE ticket_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                    ) recent
                    ORDER BY created_at ASC, id ASC
                    """
                ).format(self._table("support_ticket_messages")),
                (ticket_id, safe_limit),
            )
            return [_ticket_message_row_to_payload(row) for row in cur.fetchall()]

    def _fetch_latest_customer_message_created_at_for_trace(
        self,
        conn: psycopg.Connection[Any],
        *,
        ticket_id: str,
    ) -> str | None:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT created_at
                    FROM {}
                    WHERE ticket_id = %s
                      AND role = 'customer'
                      AND btrim(content) <> ''
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """
                ).format(self._table("support_ticket_messages")),
                (ticket_id,),
            )
            row = cur.fetchone()
        return _to_iso(row[0]) if row is not None else None

    def _fetch_trace_final_assistant_message(
        self,
        conn: psycopg.Connection[Any],
        *,
        ticket_id: str,
        message_created_at: str | None,
    ) -> dict[str, Any] | None:
        lower_bound = _clean_error_text(message_created_at)
        if not lower_bound:
            lower_bound = _clean_error_text(
                self._fetch_latest_customer_message_created_at_for_trace(
                    conn,
                    ticket_id=ticket_id,
                )
            )
        if not lower_bound:
            return None
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT ticket_id, role, content, created_at, sentiment_label, sources, citations, meta
                    FROM {}
                    WHERE ticket_id = %s
                      AND role = 'assistant'
                      AND btrim(content) <> ''
                      AND created_at >= %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """
                ).format(self._table("support_ticket_messages")),
                (ticket_id, lower_bound),
            )
            row = cur.fetchone()
        return _ticket_message_row_to_payload(row) if row is not None else None

    def _fetch_investigations(
        self,
        conn: psycopg.Connection[Any],
        ticket_ids: list[str],
        *,
        include_messages: bool,
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {ticket_id: [] for ticket_id in ticket_ids}
        if not ticket_ids:
            return grouped

        investigation_rows: list[tuple[Any, ...]]
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT
                        investigation_id,
                        ticket_id,
                        state,
                        trigger_reason,
                        trigger_source,
                        draft_customer_reply,
                        final_confirmation_requested_at,
                        opened_at,
                        updated_at,
                        closed_at
                    FROM {}
                    WHERE ticket_id = ANY(%s)
                    ORDER BY updated_at DESC, opened_at DESC
                    """
                ).format(self._table("support_ticket_investigations")),
                (ticket_ids,),
            )
            investigation_rows = cur.fetchall()

        message_map: dict[str, list[dict[str, Any]]] = {}
        investigation_ids = [str(row[0]) for row in investigation_rows]
        if include_messages and investigation_ids:
            message_map = {investigation_id: [] for investigation_id in investigation_ids}
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT message_id, investigation_id, role, content, created_at, meta
                        FROM {}
                        WHERE investigation_id = ANY(%s)
                        ORDER BY created_at ASC, id ASC
                        """
                    ).format(self._table("support_ticket_investigation_messages")),
                    (investigation_ids,),
                )
                for row in cur.fetchall():
                    investigation_id = str(row[1])
                    message_map.setdefault(investigation_id, []).append(
                        {
                            "id": str(row[0]),
                            "role": _normalize_investigation_role(row[2]),
                            "content": str(row[3]),
                            "created_at": _to_iso(row[4]),
                            "meta": row[5] if isinstance(row[5], dict) else None,
                        }
                    )

        for row in investigation_rows:
            investigation = {
                "id": str(row[0]),
                "state": str(row[2]),
                "trigger_reason": str(row[3]),
                "trigger_source": str(row[4]),
                "draft_customer_reply": str(row[5] or ""),
                "final_confirmation_requested_at": _to_iso(row[6]) if row[6] is not None else None,
                "opened_at": _to_iso(row[7]),
                "updated_at": _to_iso(row[8]),
                "closed_at": _to_iso(row[9]) if row[9] is not None else None,
                "messages": message_map.get(str(row[0]), []) if include_messages else [],
            }
            grouped[str(row[1])].append(investigation)
        return grouped

    def _fetch_engineer_case_rows(
        self,
        conn: psycopg.Connection[Any],
        *,
        engineer_case_ids: list[str] | None = None,
        ticket_ids: list[str] | None = None,
    ) -> list[tuple[Any, ...]]:
        conditions: list[sql.SQL] = []
        parameters: list[Any] = []
        if engineer_case_ids:
            conditions.append(sql.SQL("engineer_case_id = ANY(%s)"))
            parameters.append(engineer_case_ids)
        if ticket_ids:
            conditions.append(sql.SQL("client_ticket_id = ANY(%s)"))
            parameters.append(ticket_ids)

        query = sql.SQL(
            """
            SELECT
                engineer_case_id,
                client_ticket_id,
                case_sequence,
                title,
                status,
                trigger_source,
                trigger_reason,
                draft_customer_reply,
                final_confirmation_requested_at,
                engineer_handoff_packet,
                engineer_agent_state,
                opened_at,
                updated_at,
                closed_at,
                assigned_engineer_id,
                assignment_status,
                assigned_at,
                sla_due_at,
                assignment_attempt_count,
                previous_assignees,
                last_assignment_reason,
                dispatch_status,
                assignment_updated_at,
                assignment_version
            FROM {}
            """
        ).format(self._table("support_engineer_cases"))
        if conditions:
            query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
        query += sql.SQL(" ORDER BY updated_at DESC, opened_at DESC")
        with conn.cursor() as cur:
            cur.execute(query, tuple(parameters))
            return cur.fetchall()

    def _fetch_engineer_case_messages(
        self,
        conn: psycopg.Connection[Any],
        engineer_case_ids: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {engineer_case_id: [] for engineer_case_id in engineer_case_ids}
        if not engineer_case_ids:
            return grouped
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT message_id, engineer_case_id, role, content, created_at, meta
                    FROM {}
                    WHERE engineer_case_id = ANY(%s)
                    ORDER BY created_at ASC, id ASC
                    """
                ).format(self._table("support_engineer_case_messages")),
                (engineer_case_ids,),
            )
            for row in cur.fetchall():
                grouped.setdefault(str(row[1]), []).append(
                    {
                        "id": str(row[0]),
                        "role": _normalize_investigation_role(row[2]),
                        "content": str(row[3]),
                        "created_at": _to_iso(row[4]),
                        "meta": row[5] if isinstance(row[5], dict) else None,
                    }
                )
        return grouped

    def _row_to_engineer_case_record(
        self,
        row: tuple[Any, ...],
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        investigation_state = _derive_engineer_case_investigation_state(
            final_confirmation_requested_at=row[8],
            closed_at=row[13],
        )
        return {
            "engineer_case_id": str(row[0]),
            "client_ticket_id": str(row[1]),
            "case_sequence": _safe_positive_int(row[2], 1),
            "title": str(row[3]),
            "status": _normalize_status(row[4]),
            "trigger_source": str(row[5]),
            "trigger_reason": str(row[6]),
            "draft_customer_reply": str(row[7] or ""),
            "final_confirmation_requested_at": _to_iso(row[8]) if row[8] is not None else None,
            "engineer_handoff_packet": row[9] if isinstance(row[9], dict) else None,
            "engineer_agent_state": row[10] if isinstance(row[10], dict) else None,
            "opened_at": _to_iso(row[11]),
            "updated_at": _to_iso(row[12]),
            "closed_at": _to_iso(row[13]) if row[13] is not None else None,
            "assigned_engineer_id": str(row[14] or "").strip() or None if len(row) > 14 else None,
            "assignment_status": _normalize_assignment_status(
                row[15] if len(row) > 15 else None,
                assigned_engineer_id=row[14] if len(row) > 14 else None,
            ),
            "assigned_at": _to_iso(row[16]) if len(row) > 16 and row[16] is not None else None,
            "sla_due_at": _to_iso(row[17]) if len(row) > 17 and row[17] is not None else None,
            "assignment_attempt_count": _safe_non_negative_int(row[18] if len(row) > 18 else 0, 0),
            "previous_assignees": _normalize_previous_assignees(row[19] if len(row) > 19 else []),
            "last_assignment_reason": str(row[20] or "").strip() or None if len(row) > 20 else None,
            "dispatch_status": _normalize_dispatch_status(
                row[21] if len(row) > 21 else None,
                assignment_status=row[15] if len(row) > 15 else None,
            ),
            "assignment_updated_at": (
                _to_iso(row[22]) if len(row) > 22 and row[22] is not None else None
            ),
            "assignment_version": _safe_non_negative_int(row[23] if len(row) > 23 else 0, 0),
            "investigation_state": investigation_state,
            "messages": messages,
        }

    def _row_to_ticket(
        self,
        row: tuple[Any, ...],
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        has_product_column = len(row) >= 11
        has_product_selection_state_column = len(row) >= 14
        has_client_intake_state_column = len(row) >= 12
        has_client_agent_runtime_state_column = len(row) >= 13
        product = _normalize_product(row[8]) if has_product_column else None
        if has_product_selection_state_column:
            product_selection_state = _normalize_product_selection_state(row[9])
            client_intake_state = _normalize_client_intake_state(row[10]) if len(row) >= 11 else None
            client_agent_runtime_state = _normalize_client_agent_runtime_state(row[11]) if len(row) >= 12 else None
        else:
            product_selection_state = None
            client_intake_state = (
                _normalize_client_intake_state(row[9]) if has_client_intake_state_column else None
            )
            client_agent_runtime_state = (
                _normalize_client_agent_runtime_state(row[10]) if has_client_agent_runtime_state_column else None
            )
        created_at_index = (
            12
            if has_product_selection_state_column
            else 11
            if has_client_agent_runtime_state_column
            else 10
            if has_client_intake_state_column
            else 9
            if has_product_column
            else 8
        )
        updated_at_index = (
            13
            if has_product_selection_state_column
            else 12
            if has_client_agent_runtime_state_column
            else 11
            if has_client_intake_state_column
            else 10
            if has_product_column
            else 9
        )
        created_at = _to_iso(row[created_at_index])
        updated_at = _to_iso(row[updated_at_index])
        return {
            "ticket_id": str(row[0]),
            "customer_id": str(row[1]),
            "requester": str(row[2]),
            "subject": str(row[3]),
            "status": _normalize_status(row[4]),
            "last_engineer_action": row[5],
            "active_engineer_case_id": str(row[6]).strip() if row[6] is not None and str(row[6]).strip() else None,
            "engineer_case_count": _safe_non_negative_int(row[7], 0),
            "product": product,
            "product_selection_state": product_selection_state,
            "client_intake_state": client_intake_state,
            "client_agent_runtime_state": client_agent_runtime_state,
            "created_at": created_at,
            "updated_at": updated_at,
            "messages": messages,
        }

    def _fetch_ticket_rows(
        self,
        conn: psycopg.Connection[Any],
        *,
        ticket_ids: list[str] | None = None,
    ) -> list[tuple[Any, ...]]:
        query = sql.SQL(
            """
            SELECT
                ticket_id,
                customer_id,
                requester,
                subject,
                status,
                last_engineer_action,
                active_engineer_case_id,
                engineer_case_count,
                product,
                product_selection_state,
                client_intake_state,
                client_agent_runtime_state,
                created_at,
                updated_at
            FROM {}
            """
        ).format(self._table("support_tickets"))
        parameters: tuple[Any, ...] = ()
        if ticket_ids:
            query += sql.SQL(" WHERE ticket_id = ANY(%s)")
            parameters = (ticket_ids,)
        with conn.cursor() as cur:
            cur.execute(query, parameters)
            return cur.fetchall()

    def _fetch_ticket_events_for_trace(
        self,
        conn: psycopg.Connection[Any],
        *,
        ticket_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT ticket_id, event_type, payload, created_at
                    FROM {}
                    WHERE ticket_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """
                ).format(self._table("support_ticket_events")),
                (ticket_id, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "ticket_id": str(row[0]) if row[0] is not None else None,
                "event_type": str(row[1]),
                "payload": row[2] if isinstance(row[2], dict) else {},
                "created_at": _to_iso(row[3]),
            }
            for row in rows
        ]

    def _fetch_ticket_agent_events_for_trace(
        self,
        conn: psycopg.Connection[Any],
        *,
        ticket_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT ticket_id, message_id, run_id, agent_name, phase, event_type, payload, created_at
                    FROM {}
                    WHERE ticket_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """
                ).format(self._table("support_ticket_agent_events")),
                (ticket_id, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "ticket_id": str(row[0]) if row[0] is not None else None,
                "message_id": str(row[1]) if row[1] is not None else None,
                "run_id": str(row[2]),
                "agent_name": str(row[3]),
                "phase": str(row[4]),
                "event_type": str(row[5]),
                "payload": row[6] if isinstance(row[6], dict) else {},
                "created_at": _to_iso(row[7]),
            }
            for row in rows
        ]

    def _fetch_ticket_map(
        self,
        conn: psycopg.Connection[Any],
        ticket_ids: list[str],
        *,
        include_messages: bool,
    ) -> dict[str, dict[str, Any]]:
        rows = self._fetch_ticket_rows(conn, ticket_ids=ticket_ids)
        message_map = self._fetch_messages(conn, ticket_ids) if include_messages else {}
        return {
            str(row[0]): self._row_to_ticket(row, message_map.get(str(row[0]), []))
            for row in rows
        }

    def _row_to_ticket_header(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "ticket_id": str(row[0]),
            "customer_id": str(row[1]),
            "requester": str(row[2]),
            "subject": str(row[3]),
            "last_engineer_action": row[4],
            "created_at": _to_iso(row[5]),
            "updated_at": _to_iso(row[6]),
            "messages": [],
        }

    def _fetch_ticket_header_map(
        self,
        conn: psycopg.Connection[Any],
        ticket_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not ticket_ids:
            return {}
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT
                        ticket_id,
                        customer_id,
                        requester,
                        subject,
                        last_engineer_action,
                        created_at,
                        updated_at
                    FROM {}
                    WHERE ticket_id = ANY(%s)
                    """
                ).format(self._table("support_tickets")),
                (ticket_ids,),
            )
            rows = cur.fetchall()
        return {
            str(row[0]): self._row_to_ticket_header(row)
            for row in rows
        }

    def _fetch_tickets(self, include_messages: bool) -> list[dict[str, Any]]:
        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            rows = self._fetch_ticket_rows(conn)
            ticket_ids = [str(row[0]) for row in rows]
            message_map = self._fetch_messages(conn, ticket_ids) if include_messages else {}
            tickets: list[dict[str, Any]] = []
            for row in rows:
                ticket_id = str(row[0])
                ticket = self._row_to_ticket(
                    row,
                    message_map.get(ticket_id, []),
                )
                tickets.append(ticket)
            return tickets

        return self._run_with_connection_retry("list_tickets", _operation)

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            rows = self._fetch_ticket_rows(conn, ticket_ids=[ticket_id])
            row = rows[0] if rows else None
            if row is None:
                return None
            message_map = self._fetch_messages(conn, [ticket_id])
            return self._row_to_ticket(
                row,
                message_map.get(ticket_id, []),
            )

        return self._run_with_connection_retry("get_ticket", _operation)

    def list_tickets(self, include_messages: bool = True) -> list[dict[str, Any]]:
        return self._fetch_tickets(include_messages=include_messages)

    def get_trace_ticket_snapshot(
        self,
        ticket_id: str,
        *,
        event_limit: int = 100,
        message_created_at: str | None = None,
        include_messages: bool = False,
        message_limit: int = 0,
    ) -> dict[str, Any] | None:
        safe_event_limit = _safe_positive_int(event_limit, 100)
        safe_message_limit = _safe_non_negative_int(message_limit, 0)

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            rows = self._fetch_ticket_rows(conn, ticket_ids=[ticket_id])
            row = rows[0] if rows else None
            if row is None:
                return None
            ticket_messages: list[dict[str, Any]]
            if include_messages:
                if safe_message_limit > 0:
                    ticket_messages = self._fetch_recent_messages_for_trace(
                        conn,
                        ticket_id=ticket_id,
                        limit=safe_message_limit,
                    )
                else:
                    ticket_messages = self._fetch_messages(conn, [ticket_id]).get(ticket_id, [])
            else:
                ticket_messages = []
            final_assistant = self._fetch_trace_final_assistant_message(
                conn,
                ticket_id=ticket_id,
                message_created_at=message_created_at,
            )
            ticket = self._row_to_ticket(
                row,
                ticket_messages,
            )
            return _build_trace_ticket_snapshot_payload(
                ticket=ticket,
                ticket_events=self._fetch_ticket_events_for_trace(conn, ticket_id=ticket_id, limit=safe_event_limit),
                agent_events=self._fetch_ticket_agent_events_for_trace(conn, ticket_id=ticket_id, limit=safe_event_limit),
                message_created_at=message_created_at,
                include_messages=include_messages,
                message_limit=safe_message_limit,
                final_assistant=final_assistant,
            )

        return self._run_with_connection_retry("get_trace_ticket_snapshot", _operation)

    def save_ticket(
        self,
        ticket: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        ticket_id = str(ticket.get("ticket_id", "")).strip()
        if not ticket_id:
            raise ValueError("ticket_id is required")

        created_at = ticket.get("created_at") or _utc_now()
        updated_at = ticket.get("updated_at") or _utc_now()
        requester = str(ticket.get("requester") or ticket.get("customer_id") or "Unknown")
        subject = str(ticket.get("subject") or "General support request")
        status = _normalize_status(ticket.get("status"))
        last_action = ticket.get("last_engineer_action")
        active_engineer_case_id = str(ticket.get("active_engineer_case_id") or "").strip() or None
        engineer_case_count = _safe_non_negative_int(ticket.get("engineer_case_count"), 0)
        product = _normalize_product(ticket.get("product"))
        product_selection_state = _normalize_product_selection_state(ticket.get("product_selection_state"))
        client_intake_state = _normalize_client_intake_state(ticket.get("client_intake_state"))
        client_agent_runtime_state = _normalize_client_agent_runtime_state(ticket.get("client_agent_runtime_state"))

        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                ticket_id,
                                customer_id,
                                requester,
                                subject,
                                status,
                                last_engineer_action,
                                active_engineer_case_id,
                                engineer_case_count,
                                product,
                                product_selection_state,
                                client_intake_state,
                                client_agent_runtime_state,
                                created_at,
                                updated_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (ticket_id) DO UPDATE SET
                                customer_id = EXCLUDED.customer_id,
                                requester = EXCLUDED.requester,
                                subject = EXCLUDED.subject,
                                status = EXCLUDED.status,
                                last_engineer_action = EXCLUDED.last_engineer_action,
                                active_engineer_case_id = EXCLUDED.active_engineer_case_id,
                                engineer_case_count = EXCLUDED.engineer_case_count,
                                product = EXCLUDED.product,
                                product_selection_state = EXCLUDED.product_selection_state,
                                client_intake_state = EXCLUDED.client_intake_state,
                                client_agent_runtime_state = EXCLUDED.client_agent_runtime_state,
                                updated_at = EXCLUDED.updated_at
                            """
                        ).format(self._table("support_tickets")),
                        (
                            ticket_id,
                            str(ticket.get("customer_id") or "C-001"),
                            requester,
                            subject,
                            status,
                            Json(last_action) if isinstance(last_action, dict) else None,
                            active_engineer_case_id,
                            engineer_case_count,
                            product,
                            Json(product_selection_state) if product_selection_state else None,
                            Json(client_intake_state) if client_intake_state else None,
                            Json(client_agent_runtime_state) if client_agent_runtime_state else None,
                            created_at,
                            updated_at,
                        ),
                    )

                    for message in new_messages or []:
                        content = str(message.get("content", "")).strip()
                        if not content:
                            continue
                        sentiment_label = _normalize_message_sentiment_label(
                            message.get("sentiment_label")
                        )
                        sources = message.get("sources")
                        citations = message.get("citations")
                        meta = _ticket_message_meta(message)
                        cur.execute(
                            sql.SQL(
                                """
                                INSERT INTO {} (
                                    ticket_id,
                                    role,
                                    content,
                                    created_at,
                                    sentiment_label,
                                    sources,
                                    citations,
                                    meta
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                """
                            ).format(self._table("support_ticket_messages")),
                            (
                                ticket_id,
                                _normalize_role(message.get("role")),
                                content,
                                message.get("created_at") or updated_at,
                                sentiment_label,
                                Json(sources) if sources else None,
                                Json(citations) if citations else None,
                                Json(meta),
                            ),
                        )

        self._run_with_connection_retry("save_ticket", _operation)

    def update_message_sentiment_label(
        self,
        *,
        ticket_id: str,
        role: str,
        content: str,
        created_at: str,
        sentiment_label: str,
    ) -> bool:
        normalized_label = _normalize_message_sentiment_label(sentiment_label)
        if normalized_label is None:
            return False

        def _operation(conn: psycopg.Connection[Any]) -> bool:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            WITH target AS (
                                SELECT id
                                FROM {}
                                WHERE ticket_id = %s
                                  AND role = %s
                                  AND content = %s
                                  AND created_at = %s
                                ORDER BY id DESC
                                LIMIT 1
                            )
                            UPDATE {} AS message
                            SET sentiment_label = %s
                            FROM target
                            WHERE message.id = target.id
                              AND message.sentiment_label IS DISTINCT FROM %s
                            """
                        ).format(
                            self._table("support_ticket_messages"),
                            self._table("support_ticket_messages"),
                        ),
                        (
                            str(ticket_id).strip(),
                            _normalize_role(role),
                            str(content).strip(),
                            str(created_at).strip(),
                            normalized_label,
                            normalized_label,
                        ),
                    )
                    return cur.rowcount > 0

        return self._run_with_connection_retry("update_message_sentiment_label", _operation)

    def get_engineer_case(
        self,
        engineer_case_id: str,
        *,
        include_client_messages: bool = True,
    ) -> dict[str, Any] | None:
        normalized_case_id = str(engineer_case_id).strip()

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            rows = self._fetch_engineer_case_rows(conn, engineer_case_ids=[normalized_case_id])
            if not rows:
                return None
            row = rows[0]
            record = self._row_to_engineer_case_record(
                row,
                self._fetch_engineer_case_messages(conn, [normalized_case_id]).get(normalized_case_id, []),
            )
            ticket_map = self._fetch_ticket_map(
                conn,
                [str(record.get("client_ticket_id") or "")],
                include_messages=include_client_messages,
            )
            client_ticket = ticket_map.get(str(record.get("client_ticket_id") or ""))
            return _engineer_case_record_to_payload(
                record,
                client_ticket=client_ticket,
                include_client_messages=include_client_messages,
            )

        return self._run_with_connection_retry("get_engineer_case", _operation)

    def list_engineer_cases(
        self,
        *,
        include_client_messages: bool = True,
        include_investigation_messages: bool = True,
    ) -> list[dict[str, Any]]:
        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            rows = self._fetch_engineer_case_rows(conn)
            case_ids = [str(row[0]) for row in rows]
            message_map = (
                self._fetch_engineer_case_messages(conn, case_ids)
                if include_investigation_messages
                else {}
            )
            client_ticket_ids = sorted({str(row[1]) for row in rows})
            client_ticket_map = self._fetch_ticket_map(
                conn,
                client_ticket_ids,
                include_messages=include_client_messages,
            )
            payloads: list[dict[str, Any]] = []
            for row in rows:
                record = self._row_to_engineer_case_record(
                    row,
                    message_map.get(str(row[0]), []),
                )
                payloads.append(
                    _engineer_case_record_to_payload(
                        record,
                        client_ticket=client_ticket_map.get(str(record.get("client_ticket_id") or "")),
                        include_client_messages=include_client_messages,
                    )
                )
            return payloads

        return self._run_with_connection_retry("list_engineer_cases", _operation)

    def list_engineer_case_headers(self) -> list[dict[str, Any]]:
        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            rows = self._fetch_engineer_case_rows(conn)
            client_ticket_ids = sorted({str(row[1]) for row in rows})
            client_ticket_map = self._fetch_ticket_header_map(conn, client_ticket_ids)
            payloads: list[dict[str, Any]] = []
            for row in rows:
                record = self._row_to_engineer_case_record(row, [])
                payloads.append(
                    _engineer_case_record_to_header_payload(
                        record,
                        client_ticket=client_ticket_map.get(str(record.get("client_ticket_id") or "")),
                    )
                )
            return payloads

        return self._run_with_connection_retry("list_engineer_case_headers", _operation)

    def list_ticket_engineer_cases(
        self,
        ticket_id: str,
        *,
        include_client_messages: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_ticket_id = str(ticket_id).strip()

        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            rows = self._fetch_engineer_case_rows(conn, ticket_ids=[normalized_ticket_id])
            case_ids = [str(row[0]) for row in rows]
            message_map = self._fetch_engineer_case_messages(conn, case_ids)
            client_ticket = self._fetch_ticket_map(
                conn,
                [normalized_ticket_id],
                include_messages=include_client_messages,
            ).get(normalized_ticket_id)
            payloads: list[dict[str, Any]] = []
            for row in rows:
                record = self._row_to_engineer_case_record(
                    row,
                    message_map.get(str(row[0]), []),
                )
                payloads.append(
                    _engineer_case_record_to_payload(
                        record,
                        client_ticket=client_ticket,
                        include_client_messages=include_client_messages,
                    )
                )
            return payloads

        return self._run_with_connection_retry("list_ticket_engineer_cases", _operation)

    def get_active_engineer_case(
        self,
        ticket_id: str,
        *,
        include_client_messages: bool = True,
    ) -> dict[str, Any] | None:
        normalized_ticket_id = str(ticket_id).strip()

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            rows = self._fetch_engineer_case_rows(conn, ticket_ids=[normalized_ticket_id])
            if not rows:
                return None
            message_map = self._fetch_engineer_case_messages(conn, [str(row[0]) for row in rows])
            client_ticket = self._fetch_ticket_map(
                conn,
                [normalized_ticket_id],
                include_messages=include_client_messages,
            ).get(normalized_ticket_id)
            for row in rows:
                record = self._row_to_engineer_case_record(
                    row,
                    message_map.get(str(row[0]), []),
                )
                if _normalize_investigation_state(record.get("investigation_state")) == "closed":
                    continue
                return _engineer_case_record_to_payload(
                    record,
                    client_ticket=client_ticket,
                    include_client_messages=include_client_messages,
                )
            return None

        return self._run_with_connection_retry("get_active_engineer_case", _operation)

    def save_engineer_case(
        self,
        engineer_case: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        saved = copy.deepcopy(engineer_case)
        engineer_case_id = str(saved.get("engineer_case_id") or "").strip()
        if not engineer_case_id:
            raise ValueError("engineer_case_id is required")
        client_ticket_id = str(saved.get("client_ticket_id") or "").strip()
        if not client_ticket_id:
            raise ValueError("client_ticket_id is required")
        case_sequence = _case_sequence_from_identifiers(engineer_case_id, saved.get("case_sequence"))
        status = _normalize_status(saved.get("status"))
        trigger_source = str(saved.get("trigger_source") or "support_query").strip() or "support_query"
        trigger_reason = str(saved.get("trigger_reason") or "unknown").strip() or "unknown"
        draft_customer_reply = str(saved.get("draft_customer_reply") or "").strip()
        final_confirmation_requested_at = saved.get("final_confirmation_requested_at")
        engineer_handoff_packet = saved.get("engineer_handoff_packet") if isinstance(saved.get("engineer_handoff_packet"), dict) else None
        engineer_agent_state = saved.get("engineer_agent_state") if isinstance(saved.get("engineer_agent_state"), dict) else None
        assigned_engineer_id = str(saved.get("assigned_engineer_id") or "").strip() or None
        assignment_status = _normalize_assignment_status(
            saved.get("assignment_status"), assigned_engineer_id=assigned_engineer_id
        )
        if status == "resolved" or saved.get("closed_at") is not None:
            assignment_status = "resolved"
        assigned_at = saved.get("assigned_at")
        sla_due_at = saved.get("sla_due_at")
        assignment_attempt_count = _safe_non_negative_int(saved.get("assignment_attempt_count"), 0)
        previous_assignees = _normalize_previous_assignees(saved.get("previous_assignees"))
        last_assignment_reason = str(saved.get("last_assignment_reason") or "").strip() or None
        dispatch_status = _normalize_dispatch_status(
            saved.get("dispatch_status"), assignment_status=assignment_status
        )
        assignment_updated_at = saved.get("assignment_updated_at")
        assignment_version = _safe_non_negative_int(saved.get("assignment_version"), 0)
        opened_at = saved.get("opened_at") or _utc_now()
        updated_at = saved.get("updated_at") or opened_at
        closed_at = saved.get("closed_at")

        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                engineer_case_id,
                                client_ticket_id,
                                case_sequence,
                                title,
                                status,
                                trigger_source,
                                trigger_reason,
                                draft_customer_reply,
                                final_confirmation_requested_at,
                                engineer_handoff_packet,
                                engineer_agent_state,
                                opened_at,
                                updated_at,
                                closed_at,
                                assigned_engineer_id,
                                assignment_status,
                                assigned_at,
                                sla_due_at,
                                assignment_attempt_count,
                                previous_assignees,
                                last_assignment_reason,
                                dispatch_status,
                                assignment_updated_at,
                                assignment_version
                            )
                            VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                            )
                            ON CONFLICT (engineer_case_id) DO UPDATE SET
                                client_ticket_id = EXCLUDED.client_ticket_id,
                                case_sequence = EXCLUDED.case_sequence,
                                title = EXCLUDED.title,
                                status = EXCLUDED.status,
                                trigger_source = EXCLUDED.trigger_source,
                                trigger_reason = EXCLUDED.trigger_reason,
                                draft_customer_reply = EXCLUDED.draft_customer_reply,
                                final_confirmation_requested_at = EXCLUDED.final_confirmation_requested_at,
                                engineer_handoff_packet = EXCLUDED.engineer_handoff_packet,
                                engineer_agent_state = EXCLUDED.engineer_agent_state,
                                updated_at = EXCLUDED.updated_at,
                                closed_at = EXCLUDED.closed_at,
                                assigned_engineer_id = support_engineer_cases.assigned_engineer_id,
                                assignment_status = support_engineer_cases.assignment_status,
                                dispatch_status = support_engineer_cases.dispatch_status
                            """
                        ).format(self._table("support_engineer_cases")),
                        (
                            engineer_case_id,
                            client_ticket_id,
                            case_sequence,
                            str(saved.get("title") or "").strip() or "Engineer case",
                            status,
                            trigger_source,
                            trigger_reason,
                            draft_customer_reply,
                            final_confirmation_requested_at,
                            Json(engineer_handoff_packet) if engineer_handoff_packet else None,
                            Json(engineer_agent_state) if engineer_agent_state else None,
                            opened_at,
                            updated_at,
                            closed_at,
                            assigned_engineer_id,
                            assignment_status,
                            assigned_at,
                            sla_due_at,
                            assignment_attempt_count,
                            Json(previous_assignees),
                            last_assignment_reason,
                            dispatch_status,
                            assignment_updated_at,
                            assignment_version,
                        ),
                    )
                    for message in new_messages or []:
                        content = str(message.get("content") or "").strip()
                        if not content:
                            continue
                        cur.execute(
                            sql.SQL(
                                """
                                INSERT INTO {} (
                                    message_id,
                                    engineer_case_id,
                                    role,
                                    content,
                                    created_at,
                                    meta
                                )
                                VALUES (%s, %s, %s, %s, %s, %s)
                                """
                            ).format(self._table("support_engineer_case_messages")),
                            (
                                str(message.get("id") or f"{engineer_case_id}-{uuid4().hex[:8]}"),
                                engineer_case_id,
                                _normalize_investigation_role(message.get("role")),
                                content,
                                message.get("created_at") or updated_at,
                                Json(message.get("meta")) if isinstance(message.get("meta"), dict) else None,
                            ),
                        )

        self._run_with_connection_retry("save_engineer_case", _operation)

    def claim_engineer_case(
        self,
        engineer_case_id: str,
        engineer_id: str,
        *,
        updated_at: str,
    ) -> bool:
        normalized_case_id = str(engineer_case_id or "").strip()
        normalized_engineer_id = str(engineer_id or "").strip()
        if not normalized_case_id or not normalized_engineer_id:
            return False

        def _operation(conn: psycopg.Connection[Any]) -> bool:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET assigned_engineer_id = %s, updated_at = %s
                            WHERE engineer_case_id = %s
                              AND status <> 'resolved'
                              AND (assigned_engineer_id IS NULL OR assigned_engineer_id = %s)
                            """
                        ).format(self._table("support_engineer_cases")),
                        (
                            normalized_engineer_id,
                            updated_at,
                            normalized_case_id,
                            normalized_engineer_id,
                        ),
                    )
                    return cur.rowcount > 0

        return self._run_with_connection_retry("claim_engineer_case", _operation)

    def update_engineer_case_assignment(
        self,
        engineer_case_id: str,
        *,
        expected_version: int | None,
        assignment_status: str,
        assigned_engineer_id: str | None,
        assigned_at: str | None,
        sla_due_at: str | None,
        reason: str,
        updated_at: str,
        actor: str,
        event_type: str,
        dispatch_status: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_case_id = str(engineer_case_id or "").strip()
        normalized_status = _normalize_assignment_status(assignment_status)
        normalized_engineer_id = str(assigned_engineer_id or "").strip() or None
        if not normalized_case_id:
            return None
        if normalized_status == "assigned" and not normalized_engineer_id:
            raise ValueError("assigned_engineer_id is required for assigned cases")

        def _operation(conn: psycopg.Connection[Any]) -> bool:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            SELECT assignment_status, assigned_engineer_id,
                                   assignment_version, assignment_attempt_count,
                                   previous_assignees, assigned_at
                            FROM {}
                            WHERE engineer_case_id = %s
                            FOR UPDATE
                            """
                        ).format(self._table("support_engineer_cases")),
                        (normalized_case_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return False
                    current_version = _safe_non_negative_int(row[2], 0)
                    if expected_version is not None and current_version != expected_version:
                        return False
                    previous_status = _normalize_assignment_status(
                        row[0], assigned_engineer_id=row[1]
                    )
                    previous_engineer_id = str(row[1] or "").strip() or None
                    previous_assignees = _normalize_previous_assignees(row[4])
                    if (
                        previous_engineer_id
                        and previous_engineer_id != normalized_engineer_id
                        and previous_engineer_id not in previous_assignees
                    ):
                        previous_assignees.append(previous_engineer_id)
                    next_version = current_version + 1
                    next_attempt_count = _safe_non_negative_int(row[3], 0) + (
                        1 if normalized_status == "assigned" else 0
                    )
                    normalized_reason = str(reason or "").strip() or "assignment_update"
                    normalized_event_type = str(event_type or "").strip() or "engineer_case_assignment_changed"
                    normalized_actor = str(actor or "").strip() or "system"
                    normalized_dispatch_status = _normalize_dispatch_status(
                        dispatch_status, assignment_status=normalized_status
                    )
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET assignment_status = %s,
                                assigned_engineer_id = %s,
                                assigned_at = %s,
                                sla_due_at = %s,
                                assignment_attempt_count = %s,
                                previous_assignees = %s,
                                last_assignment_reason = %s,
                                dispatch_status = %s,
                                assignment_updated_at = %s,
                                assignment_version = %s,
                                updated_at = %s
                            WHERE engineer_case_id = %s
                              AND assignment_version = %s
                            """
                        ).format(self._table("support_engineer_cases")),
                        (
                            normalized_status,
                            normalized_engineer_id,
                            (
                                assigned_at
                                if normalized_status == "assigned"
                                else row[5]
                                if normalized_status == "resolved"
                                else None
                            ),
                            sla_due_at if normalized_status == "assigned" else None,
                            next_attempt_count,
                            Json(previous_assignees),
                            normalized_reason,
                            normalized_dispatch_status,
                            updated_at,
                            next_version,
                            updated_at,
                            normalized_case_id,
                            current_version,
                        ),
                    )
                    if cur.rowcount != 1:
                        return False
                    event = {
                        "event": normalized_event_type,
                        "engineer_case_id": normalized_case_id,
                        "actor": normalized_actor,
                        "reason": normalized_reason,
                        "previous_assignment_status": previous_status,
                        "assignment_status": normalized_status,
                        "previous_assigned_engineer_id": previous_engineer_id,
                        "assigned_engineer_id": normalized_engineer_id,
                        "assignment_version": next_version,
                        "created_at": updated_at,
                    }
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (engineer_case_id, event_type, payload, created_at)
                            VALUES (%s, %s, %s, %s)
                            """
                        ).format(self._table("support_engineer_case_events")),
                        (normalized_case_id, normalized_event_type, Json(event), updated_at),
                    )
                    return True

        updated = self._run_with_connection_retry("update_engineer_case_assignment", _operation)
        if not updated:
            return None
        return self.get_engineer_case(normalized_case_id, include_client_messages=False)

    def save_workspace_account(self, account: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_workspace_account(account)
        if not normalized["password_hash"]:
            raise ValueError("password_hash is required")

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} AS existing_account (
                                account_id, display_name, role, password_hash, active,
                                availability, availability_reason, availability_updated_at,
                                last_assigned_at, created_at, updated_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (account_id) DO UPDATE SET
                                display_name = EXCLUDED.display_name,
                                role = EXCLUDED.role,
                                password_hash = CASE
                                    WHEN EXCLUDED.password_hash = '' THEN existing_account.password_hash
                                    ELSE EXCLUDED.password_hash
                                END,
                                active = EXCLUDED.active,
                                last_assigned_at = COALESCE(
                                    EXCLUDED.last_assigned_at,
                                    existing_account.last_assigned_at
                                ),
                                updated_at = EXCLUDED.updated_at
                            RETURNING account_id, display_name, role, password_hash, active,
                                      availability, availability_reason, availability_updated_at,
                                      last_assigned_at, created_at, updated_at
                            """
                        ).format(self._table("support_workspace_accounts")),
                        (
                            normalized["account_id"],
                            normalized["display_name"],
                            normalized["role"],
                            normalized["password_hash"],
                            normalized["active"],
                            normalized["availability"],
                            normalized["availability_reason"],
                            normalized["availability_updated_at"],
                            normalized["last_assigned_at"],
                            normalized["created_at"],
                            normalized["updated_at"],
                        ),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    return _workspace_account_row_to_payload(row)

        return self._run_with_connection_retry("save_workspace_account", _operation)

    def get_workspace_account(self, account_id: str) -> dict[str, Any] | None:
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            return None

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT account_id, display_name, role, password_hash, active,
                               availability, availability_reason, availability_updated_at,
                               last_assigned_at, created_at, updated_at
                        FROM {}
                        WHERE account_id = %s
                        """
                    ).format(self._table("support_workspace_accounts")),
                    (normalized_account_id,),
                )
                row = cur.fetchone()
                return _workspace_account_row_to_payload(row) if row is not None else None

        return self._run_with_connection_retry("get_workspace_account", _operation)

    def list_workspace_accounts(self) -> list[dict[str, Any]]:
        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT account_id, display_name, role, password_hash, active,
                               availability, availability_reason, availability_updated_at,
                               last_assigned_at, created_at, updated_at
                        FROM {}
                        ORDER BY LOWER(display_name), account_id
                        """
                    ).format(self._table("support_workspace_accounts"))
                )
                return [_workspace_account_row_to_payload(row) for row in cur.fetchall()]

        return self._run_with_connection_retry("list_workspace_accounts", _operation)

    def set_engineer_availability(
        self,
        account_id: str,
        *,
        availability: str,
        reason: str | None,
        actor_id: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        normalized_account_id = str(account_id or "").strip()
        normalized_availability = _normalize_engineer_availability(availability)
        normalized_reason = str(reason or "").strip() or None

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            SELECT availability, availability_reason
                            FROM {}
                            WHERE account_id = %s AND role = 'engineer'
                            FOR UPDATE
                            """
                        ).format(self._table("support_workspace_accounts")),
                        (normalized_account_id,),
                    )
                    previous = cur.fetchone()
                    if previous is None:
                        return None
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET availability = %s,
                                availability_reason = %s,
                                availability_updated_at = %s,
                                updated_at = %s
                            WHERE account_id = %s
                            RETURNING account_id, display_name, role, password_hash, active,
                                      availability, availability_reason, availability_updated_at,
                                      last_assigned_at, created_at, updated_at
                            """
                        ).format(self._table("support_workspace_accounts")),
                        (
                            normalized_availability,
                            normalized_reason,
                            updated_at,
                            updated_at,
                            normalized_account_id,
                        ),
                    )
                    row = cur.fetchone()
                    audit_payload = {
                        "previous_availability": _normalize_engineer_availability(previous[0]),
                        "availability": normalized_availability,
                        "previous_reason": str(previous[1] or "").strip() or None,
                        "reason": normalized_reason,
                    }
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (event_type, actor_id, target_id, payload, created_at)
                            VALUES (%s, %s, %s, %s, %s)
                            """
                        ).format(self._table("support_workspace_audit_events")),
                        (
                            "engineer_availability_changed",
                            str(actor_id or "system").strip() or "system",
                            normalized_account_id,
                            Json(audit_payload),
                            updated_at,
                        ),
                    )
                    return _workspace_account_row_to_payload(row) if row is not None else None

        return self._run_with_connection_retry("set_engineer_availability", _operation)

    def record_workspace_audit_event(
        self,
        event_type: str,
        *,
        actor_id: str,
        target_id: str | None,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (event_type, actor_id, target_id, payload, created_at)
                            VALUES (%s, %s, %s, %s, %s)
                            """
                        ).format(self._table("support_workspace_audit_events")),
                        (
                            str(event_type or "workspace_event").strip() or "workspace_event",
                            str(actor_id or "system").strip() or "system",
                            str(target_id or "").strip() or None,
                            Json(payload),
                            created_at,
                        ),
                    )

        self._run_with_connection_retry("record_workspace_audit_event", _operation)

    def list_workspace_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 100), 1000))

        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT event_type, actor_id, target_id, payload, created_at
                        FROM {}
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_workspace_audit_events")),
                    (safe_limit,),
                )
                return [
                    {
                        "event_type": str(row[0]),
                        "actor_id": str(row[1]),
                        "target_id": str(row[2] or "").strip() or None,
                        "payload": row[3] if isinstance(row[3], dict) else {},
                        "created_at": _to_iso(row[4]),
                    }
                    for row in cur.fetchall()
                ]

        return self._run_with_connection_retry("list_workspace_audit_events", _operation)

    def begin_idempotent_request(
        self,
        scope: str,
        idempotency_key: str,
        *,
        created_at: str,
    ) -> dict[str, Any]:
        normalized_scope = str(scope or "").strip()
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_scope or not normalized_key:
            raise ValueError("scope and idempotency_key are required")

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                scope, idempotency_key, state, response_payload,
                                created_at, updated_at
                            )
                            VALUES (%s, %s, 'processing', NULL, %s, %s)
                            ON CONFLICT (scope, idempotency_key) DO NOTHING
                            RETURNING scope
                            """
                        ).format(self._table("support_idempotency_records")),
                        (normalized_scope, normalized_key, created_at, created_at),
                    )
                    created = cur.fetchone() is not None
                    cur.execute(
                        sql.SQL(
                            """
                            SELECT scope, idempotency_key, state, response_payload,
                                   created_at, updated_at
                            FROM {}
                            WHERE scope = %s AND idempotency_key = %s
                            """
                        ).format(self._table("support_idempotency_records")),
                        (normalized_scope, normalized_key),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    return {
                        "scope": str(row[0]),
                        "idempotency_key": str(row[1]),
                        "state": str(row[2]),
                        "response_payload": row[3] if isinstance(row[3], dict) else None,
                        "created_at": _to_iso(row[4]),
                        "updated_at": _to_iso(row[5]),
                        "created": created,
                    }

        return self._run_with_connection_retry("begin_idempotent_request", _operation)

    def complete_idempotent_request(
        self,
        scope: str,
        idempotency_key: str,
        *,
        response_payload: dict[str, Any],
        updated_at: str,
    ) -> None:
        normalized_scope = str(scope or "").strip()
        normalized_key = str(idempotency_key or "").strip()

        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET state = 'completed', response_payload = %s, updated_at = %s
                            WHERE scope = %s AND idempotency_key = %s
                            """
                        ).format(self._table("support_idempotency_records")),
                        (Json(response_payload), updated_at, normalized_scope, normalized_key),
                    )
                    if cur.rowcount != 1:
                        raise ValueError("idempotency request was not started")

        self._run_with_connection_retry("complete_idempotent_request", _operation)

    def record_rollout_event(
        self,
        counter_key: str,
        event_key: str,
        *,
        created_at: str,
    ) -> tuple[int, bool]:
        normalized_counter_key = str(counter_key or "").strip()
        normalized_event_key = str(event_key or "").strip()
        if not normalized_counter_key or not normalized_event_key:
            raise ValueError("counter_key and event_key are required")

        def _operation(conn: psycopg.Connection[Any]) -> tuple[int, bool]:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            SELECT position
                            FROM {}
                            WHERE counter_key = %s AND event_key = %s
                            """
                        ).format(self._table("support_rollout_events")),
                        (normalized_counter_key, normalized_event_key),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        return int(existing[0]), False
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (counter_key, current_value, updated_at)
                            VALUES (%s, 0, %s)
                            ON CONFLICT (counter_key) DO NOTHING
                            """
                        ).format(self._table("support_rollout_counters")),
                        (normalized_counter_key, created_at),
                    )
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET current_value = current_value + 1, updated_at = %s
                            WHERE counter_key = %s
                            RETURNING current_value
                            """
                        ).format(self._table("support_rollout_counters")),
                        (created_at, normalized_counter_key),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    position = int(row[0])
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (counter_key, event_key, position, created_at)
                            VALUES (%s, %s, %s, %s)
                            """
                        ).format(self._table("support_rollout_events")),
                        (normalized_counter_key, normalized_event_key, position, created_at),
                    )
                    return position, True

        return self._run_with_connection_retry("record_rollout_event", _operation)

    def record_engineer_case_event(
        self,
        engineer_case_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (engineer_case_id, event_type, payload)
                            VALUES (%s, %s, %s)
                            """
                        ).format(self._table("support_engineer_case_events")),
                        (engineer_case_id, event_type, Json(payload)),
                    )

        self._run_with_connection_retry("record_engineer_case_event", _operation)

    def list_engineer_case_events(
        self,
        engineer_case_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)

        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT engineer_case_id, event_type, payload, created_at
                        FROM {}
                        WHERE engineer_case_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_engineer_case_events")),
                    (engineer_case_id, safe_limit),
                )
                rows = cur.fetchall()
            events: list[dict[str, Any]] = []
            for row in rows:
                events.append(
                    {
                        "engineer_case_id": str(row[0]) if row[0] is not None else None,
                        "event_type": str(row[1]),
                        "payload": row[2] if isinstance(row[2], dict) else {},
                        "created_at": _to_iso(row[3]),
                    }
                )
            return events

        return self._run_with_connection_retry("list_engineer_case_events", _operation)

    def record_engineer_hitl_feedback(self, feedback: dict[str, Any]) -> dict[str, Any]:
        saved = _normalize_engineer_hitl_feedback(feedback)

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                feedback_id,
                                engineer_case_id,
                                client_ticket_id,
                                run_id,
                                message_id,
                                evidence_packet_id,
                                feedback_type,
                                diagnosis_correctness,
                                root_cause_correctness,
                                evidence_quality,
                                citation_quality,
                                customer_reply_quality,
                                missing_information,
                                incorrect_claims,
                                corrected_root_cause,
                                corrected_solution,
                                corrected_customer_reply,
                                evidence_refs,
                                memory_candidate,
                                memory_safety,
                                memory_notes,
                                prompt_version,
                                workflow_version,
                                tool_policy_version,
                                rag_access_policy_version,
                                evidence_packet_version,
                                created_by,
                                created_at
                            )
                            VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                            )
                            ON CONFLICT (feedback_id) DO UPDATE SET
                                engineer_case_id = EXCLUDED.engineer_case_id,
                                client_ticket_id = EXCLUDED.client_ticket_id,
                                run_id = EXCLUDED.run_id,
                                message_id = EXCLUDED.message_id,
                                evidence_packet_id = EXCLUDED.evidence_packet_id,
                                feedback_type = EXCLUDED.feedback_type,
                                diagnosis_correctness = EXCLUDED.diagnosis_correctness,
                                root_cause_correctness = EXCLUDED.root_cause_correctness,
                                evidence_quality = EXCLUDED.evidence_quality,
                                citation_quality = EXCLUDED.citation_quality,
                                customer_reply_quality = EXCLUDED.customer_reply_quality,
                                missing_information = EXCLUDED.missing_information,
                                incorrect_claims = EXCLUDED.incorrect_claims,
                                corrected_root_cause = EXCLUDED.corrected_root_cause,
                                corrected_solution = EXCLUDED.corrected_solution,
                                corrected_customer_reply = EXCLUDED.corrected_customer_reply,
                                evidence_refs = EXCLUDED.evidence_refs,
                                memory_candidate = EXCLUDED.memory_candidate,
                                memory_safety = EXCLUDED.memory_safety,
                                memory_notes = EXCLUDED.memory_notes,
                                prompt_version = EXCLUDED.prompt_version,
                                workflow_version = EXCLUDED.workflow_version,
                                tool_policy_version = EXCLUDED.tool_policy_version,
                                rag_access_policy_version = EXCLUDED.rag_access_policy_version,
                                evidence_packet_version = EXCLUDED.evidence_packet_version,
                                created_by = EXCLUDED.created_by,
                                created_at = EXCLUDED.created_at
                            """
                        ).format(self._table("support_engineer_hitl_feedback")),
                        (
                            saved["feedback_id"],
                            saved["engineer_case_id"],
                            saved["client_ticket_id"],
                            saved["run_id"],
                            saved["message_id"],
                            saved["evidence_packet_id"],
                            saved["feedback_type"],
                            saved["diagnosis_correctness"],
                            saved["root_cause_correctness"],
                            saved["evidence_quality"],
                            saved["citation_quality"],
                            saved["customer_reply_quality"],
                            Json(saved["missing_information"]),
                            Json(saved["incorrect_claims"]),
                            saved["corrected_root_cause"],
                            saved["corrected_solution"],
                            saved["corrected_customer_reply"],
                            Json(saved["evidence_refs"]),
                            saved["memory_candidate"],
                            saved["memory_safety"],
                            saved["memory_notes"],
                            saved["prompt_version"],
                            saved["workflow_version"],
                            saved["tool_policy_version"],
                            saved["rag_access_policy_version"],
                            saved["evidence_packet_version"],
                            saved["created_by"],
                            saved["created_at"],
                        ),
                    )
            return copy.deepcopy(saved)

        return self._run_with_connection_retry("record_engineer_hitl_feedback", _operation)

    def list_engineer_hitl_feedback(
        self,
        engineer_case_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)

        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            feedback_id,
                            engineer_case_id,
                            client_ticket_id,
                            run_id,
                            message_id,
                            evidence_packet_id,
                            feedback_type,
                            diagnosis_correctness,
                            root_cause_correctness,
                            evidence_quality,
                            citation_quality,
                            customer_reply_quality,
                            missing_information,
                            incorrect_claims,
                            corrected_root_cause,
                            corrected_solution,
                            corrected_customer_reply,
                            evidence_refs,
                            memory_candidate,
                            memory_safety,
                            memory_notes,
                            prompt_version,
                            workflow_version,
                            tool_policy_version,
                            rag_access_policy_version,
                            evidence_packet_version,
                            created_by,
                            created_at
                        FROM {}
                        WHERE engineer_case_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_engineer_hitl_feedback")),
                    (engineer_case_id, safe_limit),
                )
                rows = cur.fetchall()
            return [
                {
                    "feedback_id": str(row[0]),
                    "engineer_case_id": str(row[1]),
                    "client_ticket_id": str(row[2]),
                    "run_id": str(row[3]) if row[3] is not None else None,
                    "message_id": str(row[4]) if row[4] is not None else None,
                    "evidence_packet_id": str(row[5]) if row[5] is not None else None,
                    "feedback_type": str(row[6]),
                    "diagnosis_correctness": str(row[7]),
                    "root_cause_correctness": str(row[8]),
                    "evidence_quality": str(row[9]),
                    "citation_quality": str(row[10]),
                    "customer_reply_quality": str(row[11]),
                    "missing_information": row[12] if isinstance(row[12], list) else [],
                    "incorrect_claims": row[13] if isinstance(row[13], list) else [],
                    "corrected_root_cause": str(row[14]) if row[14] is not None else None,
                    "corrected_solution": str(row[15]) if row[15] is not None else None,
                    "corrected_customer_reply": str(row[16]) if row[16] is not None else None,
                    "evidence_refs": row[17] if isinstance(row[17], list) else [],
                    "memory_candidate": str(row[18]),
                    "memory_safety": str(row[19]),
                    "memory_notes": str(row[20]) if row[20] is not None else None,
                    "prompt_version": str(row[21]) if row[21] is not None else None,
                    "workflow_version": str(row[22]) if row[22] is not None else None,
                    "tool_policy_version": str(row[23]) if row[23] is not None else None,
                    "rag_access_policy_version": str(row[24]) if row[24] is not None else None,
                    "evidence_packet_version": str(row[25]) if row[25] is not None else None,
                    "created_by": str(row[26]),
                    "created_at": _to_iso(row[27]),
                }
                for row in rows
            ]

        return self._run_with_connection_retry("list_engineer_hitl_feedback", _operation)

    def record_case_memory_ledger(self, record: dict[str, Any]) -> dict[str, Any]:
        saved = _normalize_case_memory_ledger(record)

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                memory_record_id,
                                source_feedback_id,
                                engineer_case_id,
                                client_ticket_id,
                                feedback_type,
                                ledger_status,
                                retrieval_enabled,
                                active_memory_status,
                                symptom,
                                root_cause,
                                solution,
                                customer_safe_summary,
                                internal_only_summary,
                                evidence_refs,
                                safety_label,
                                quality_label,
                                memory_schema_version,
                                prompt_version,
                                workflow_version,
                                tool_policy_version,
                                rag_access_policy_version,
                                evidence_packet_version,
                                metadata,
                                created_at,
                                updated_at
                            )
                            VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                            )
                            ON CONFLICT (memory_record_id) DO UPDATE SET
                                source_feedback_id = EXCLUDED.source_feedback_id,
                                engineer_case_id = EXCLUDED.engineer_case_id,
                                client_ticket_id = EXCLUDED.client_ticket_id,
                                feedback_type = EXCLUDED.feedback_type,
                                ledger_status = EXCLUDED.ledger_status,
                                retrieval_enabled = EXCLUDED.retrieval_enabled,
                                active_memory_status = EXCLUDED.active_memory_status,
                                symptom = EXCLUDED.symptom,
                                root_cause = EXCLUDED.root_cause,
                                solution = EXCLUDED.solution,
                                customer_safe_summary = EXCLUDED.customer_safe_summary,
                                internal_only_summary = EXCLUDED.internal_only_summary,
                                evidence_refs = EXCLUDED.evidence_refs,
                                safety_label = EXCLUDED.safety_label,
                                quality_label = EXCLUDED.quality_label,
                                memory_schema_version = EXCLUDED.memory_schema_version,
                                prompt_version = EXCLUDED.prompt_version,
                                workflow_version = EXCLUDED.workflow_version,
                                tool_policy_version = EXCLUDED.tool_policy_version,
                                rag_access_policy_version = EXCLUDED.rag_access_policy_version,
                                evidence_packet_version = EXCLUDED.evidence_packet_version,
                                metadata = EXCLUDED.metadata,
                                created_at = EXCLUDED.created_at,
                                updated_at = EXCLUDED.updated_at
                            """
                        ).format(self._table("support_case_memory_ledger")),
                        (
                            saved["memory_record_id"],
                            saved["source_feedback_id"],
                            saved["engineer_case_id"],
                            saved["client_ticket_id"],
                            saved["feedback_type"],
                            saved["ledger_status"],
                            saved["retrieval_enabled"],
                            saved["active_memory_status"],
                            saved["symptom"],
                            saved["root_cause"],
                            saved["solution"],
                            saved["customer_safe_summary"],
                            saved["internal_only_summary"],
                            Json(saved["evidence_refs"]),
                            saved["safety_label"],
                            saved["quality_label"],
                            saved["memory_schema_version"],
                            saved["prompt_version"],
                            saved["workflow_version"],
                            saved["tool_policy_version"],
                            saved["rag_access_policy_version"],
                            saved["evidence_packet_version"],
                            Json(saved["metadata"]),
                            saved["created_at"],
                            saved["updated_at"],
                        ),
                    )
            return copy.deepcopy(saved)

        return self._run_with_connection_retry("record_case_memory_ledger", _operation)

    def list_case_memory_ledger(
        self,
        engineer_case_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)

        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            memory_record_id,
                            source_feedback_id,
                            engineer_case_id,
                            client_ticket_id,
                            feedback_type,
                            ledger_status,
                            retrieval_enabled,
                            active_memory_status,
                            symptom,
                            root_cause,
                            solution,
                            customer_safe_summary,
                            internal_only_summary,
                            evidence_refs,
                            safety_label,
                            quality_label,
                            memory_schema_version,
                            prompt_version,
                            workflow_version,
                            tool_policy_version,
                            rag_access_policy_version,
                            evidence_packet_version,
                            metadata,
                            created_at,
                            updated_at
                        FROM {}
                        WHERE engineer_case_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_case_memory_ledger")),
                    (engineer_case_id, safe_limit),
                )
                rows = cur.fetchall()
            return [
                {
                    "memory_record_id": str(row[0]),
                    "source_feedback_id": str(row[1]),
                    "engineer_case_id": str(row[2]),
                    "client_ticket_id": str(row[3]),
                    "feedback_type": str(row[4]),
                    "ledger_status": str(row[5]),
                    "retrieval_enabled": bool(row[6]),
                    "active_memory_status": str(row[7]),
                    "symptom": str(row[8]) if row[8] is not None else None,
                    "root_cause": str(row[9]) if row[9] is not None else None,
                    "solution": str(row[10]) if row[10] is not None else None,
                    "customer_safe_summary": str(row[11]) if row[11] is not None else None,
                    "internal_only_summary": str(row[12]) if row[12] is not None else None,
                    "evidence_refs": row[13] if isinstance(row[13], list) else [],
                    "safety_label": str(row[14]),
                    "quality_label": str(row[15]),
                    "memory_schema_version": str(row[16]),
                    "prompt_version": str(row[17]) if row[17] is not None else None,
                    "workflow_version": str(row[18]) if row[18] is not None else None,
                    "tool_policy_version": str(row[19]) if row[19] is not None else None,
                    "rag_access_policy_version": str(row[20]) if row[20] is not None else None,
                    "evidence_packet_version": str(row[21]) if row[21] is not None else None,
                    "metadata": row[22] if isinstance(row[22], dict) else {},
                    "created_at": _to_iso(row[23]),
                    "updated_at": _to_iso(row[24]),
                }
                for row in rows
            ]

        return self._run_with_connection_retry("list_case_memory_ledger", _operation)

    def record_engineer_replay_eval_item(self, item: dict[str, Any]) -> dict[str, Any]:
        saved = copy.deepcopy(item)
        saved.setdefault("created_at", _utc_now())
        saved.setdefault("updated_at", _utc_now())
        eval_item_id = str(saved.get("eval_item_id") or "").strip()
        if not eval_item_id:
            raise ValueError("eval_item_id is required")

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {table} (
                                eval_item_id, client_ticket_id, engineer_case_id,
                                source_summary_packet_id, source_summary_packet_version,
                                source_plan_id, source_execution_id, source_review_id,
                                review_decision, review_trace, replan_notes,
                                engineer_revise_feedback, approved_reply, guardrail_final,
                                expected_outcome, replay_input, reference_output,
                                dataset_status, schema_version, data_quality_warnings,
                                created_at, updated_at
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                            )
                            ON CONFLICT (eval_item_id) DO UPDATE SET
                                client_ticket_id = EXCLUDED.client_ticket_id,
                                engineer_case_id = EXCLUDED.engineer_case_id,
                                source_summary_packet_id = EXCLUDED.source_summary_packet_id,
                                source_summary_packet_version = EXCLUDED.source_summary_packet_version,
                                source_plan_id = EXCLUDED.source_plan_id,
                                source_execution_id = EXCLUDED.source_execution_id,
                                source_review_id = EXCLUDED.source_review_id,
                                review_decision = EXCLUDED.review_decision,
                                review_trace = EXCLUDED.review_trace,
                                replan_notes = EXCLUDED.replan_notes,
                                engineer_revise_feedback = EXCLUDED.engineer_revise_feedback,
                                approved_reply = EXCLUDED.approved_reply,
                                guardrail_final = EXCLUDED.guardrail_final,
                                expected_outcome = EXCLUDED.expected_outcome,
                                replay_input = EXCLUDED.replay_input,
                                reference_output = EXCLUDED.reference_output,
                                dataset_status = EXCLUDED.dataset_status,
                                schema_version = EXCLUDED.schema_version,
                                data_quality_warnings = EXCLUDED.data_quality_warnings,
                                updated_at = EXCLUDED.updated_at
                            """
                        ).format(table=self._table("support_engineer_replay_eval_items")),
                        (
                            saved["eval_item_id"],
                            saved.get("client_ticket_id", ""),
                            saved.get("engineer_case_id", ""),
                            saved.get("source_summary_packet_id", ""),
                            saved.get("source_summary_packet_version", ""),
                            saved.get("source_plan_id", ""),
                            saved.get("source_execution_id", ""),
                            saved.get("source_review_id", ""),
                            saved.get("review_decision", ""),
                            Json(saved.get("review_trace") or {}),
                            Json(saved.get("replan_notes") or []),
                            Json(saved.get("engineer_revise_feedback") or []),
                            saved.get("approved_reply", ""),
                            Json(saved.get("guardrail_final") or {}),
                            saved.get("expected_outcome", "resolved_with_customer_reply"),
                            Json(saved.get("replay_input") or {}),
                            Json(saved.get("reference_output") or {}),
                            saved.get("dataset_status", "candidate"),
                            saved.get("schema_version", ""),
                            Json(saved.get("data_quality_warnings") or []),
                            saved["created_at"],
                            saved["updated_at"],
                        ),
                    )
            return copy.deepcopy(saved)

        return self._run_with_connection_retry("record_engineer_replay_eval_item", _operation)

    def list_engineer_replay_eval_items(
        self,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)

        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                if status is not None:
                    cur.execute(
                        sql.SQL(
                            """
                            SELECT eval_item_id, client_ticket_id, engineer_case_id,
                                source_summary_packet_id, source_summary_packet_version,
                                source_plan_id, source_execution_id, source_review_id,
                                review_decision, review_trace, replan_notes,
                                engineer_revise_feedback, approved_reply, guardrail_final,
                                expected_outcome, replay_input, reference_output,
                                dataset_status, schema_version, data_quality_warnings,
                                created_at, updated_at
                            FROM {table}
                            WHERE dataset_status = %s
                            ORDER BY created_at DESC
                            LIMIT %s
                            """
                        ).format(table=self._table("support_engineer_replay_eval_items")),
                        (status, safe_limit),
                    )
                else:
                    cur.execute(
                        sql.SQL(
                            """
                            SELECT eval_item_id, client_ticket_id, engineer_case_id,
                                source_summary_packet_id, source_summary_packet_version,
                                source_plan_id, source_execution_id, source_review_id,
                                review_decision, review_trace, replan_notes,
                                engineer_revise_feedback, approved_reply, guardrail_final,
                                expected_outcome, replay_input, reference_output,
                                dataset_status, schema_version, data_quality_warnings,
                                created_at, updated_at
                            FROM {table}
                            ORDER BY created_at DESC
                            LIMIT %s
                            """
                        ).format(table=self._table("support_engineer_replay_eval_items")),
                        (safe_limit,),
                    )
                rows = cur.fetchall()
            return [
                {
                    "eval_item_id": str(row[0]),
                    "client_ticket_id": str(row[1]),
                    "engineer_case_id": str(row[2]),
                    "source_summary_packet_id": str(row[3]),
                    "source_summary_packet_version": str(row[4]),
                    "source_plan_id": str(row[5]),
                    "source_execution_id": str(row[6]),
                    "source_review_id": str(row[7]),
                    "review_decision": str(row[8]),
                    "review_trace": row[9] if isinstance(row[9], dict) else {},
                    "replan_notes": row[10] if isinstance(row[10], list) else [],
                    "engineer_revise_feedback": row[11] if isinstance(row[11], list) else [],
                    "approved_reply": str(row[12]),
                    "guardrail_final": row[13] if isinstance(row[13], dict) else {},
                    "expected_outcome": str(row[14]),
                    "replay_input": row[15] if isinstance(row[15], dict) else {},
                    "reference_output": row[16] if isinstance(row[16], dict) else {},
                    "dataset_status": str(row[17]),
                    "schema_version": str(row[18]),
                    "data_quality_warnings": row[19] if isinstance(row[19], list) else [],
                    "created_at": _to_iso(row[20]),
                    "updated_at": _to_iso(row[21]),
                }
                for row in rows
            ]

        return self._run_with_connection_retry("list_engineer_replay_eval_items", _operation)

    def get_engineer_replay_eval_item(self, eval_item_id: str) -> dict[str, Any] | None:
        normalized_id = str(eval_item_id).strip()

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT eval_item_id, client_ticket_id, engineer_case_id,
                            source_summary_packet_id, source_summary_packet_version,
                            source_plan_id, source_execution_id, source_review_id,
                            review_decision, review_trace, replan_notes,
                            engineer_revise_feedback, approved_reply, guardrail_final,
                            expected_outcome, replay_input, reference_output,
                            dataset_status, schema_version, data_quality_warnings,
                            created_at, updated_at
                        FROM {table}
                        WHERE eval_item_id = %s
                        """
                    ).format(table=self._table("support_engineer_replay_eval_items")),
                    (normalized_id,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return {
                "eval_item_id": str(row[0]),
                "client_ticket_id": str(row[1]),
                "engineer_case_id": str(row[2]),
                "source_summary_packet_id": str(row[3]),
                "source_summary_packet_version": str(row[4]),
                "source_plan_id": str(row[5]),
                "source_execution_id": str(row[6]),
                "source_review_id": str(row[7]),
                "review_decision": str(row[8]),
                "review_trace": row[9] if isinstance(row[9], dict) else {},
                "replan_notes": row[10] if isinstance(row[10], list) else [],
                "engineer_revise_feedback": row[11] if isinstance(row[11], list) else [],
                "approved_reply": str(row[12]),
                "guardrail_final": row[13] if isinstance(row[13], dict) else {},
                "expected_outcome": str(row[14]),
                "replay_input": row[15] if isinstance(row[15], dict) else {},
                "reference_output": row[16] if isinstance(row[16], dict) else {},
                "dataset_status": str(row[17]),
                "schema_version": str(row[18]),
                "data_quality_warnings": row[19] if isinstance(row[19], list) else [],
                "created_at": _to_iso(row[20]),
                "updated_at": _to_iso(row[21]),
            }

        return self._run_with_connection_retry("get_engineer_replay_eval_item", _operation)

    def get_active_investigation(self, ticket_id: str) -> dict[str, Any] | None:
        investigations = self.list_ticket_investigations(ticket_id=ticket_id, include_messages=True)
        for item in investigations:
            if str(item.get("state") or "").strip().lower() != "closed":
                return item
        return None

    def list_ticket_investigations(
        self,
        ticket_id: str,
        include_messages: bool = True,
    ) -> list[dict[str, Any]]:
        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            return self._fetch_investigations(
                conn,
                [ticket_id],
                include_messages=include_messages,
            ).get(ticket_id, [])

        return self._run_with_connection_retry("list_ticket_investigations", _operation)

    def save_investigation(
        self,
        ticket_id: str,
        investigation: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        investigation_id = str(investigation.get("id") or "").strip()
        if not investigation_id:
            raise ValueError("investigation.id is required")

        state = str(investigation.get("state") or "active").strip().lower()
        trigger_reason = str(investigation.get("trigger_reason") or "unspecified").strip() or "unspecified"
        trigger_source = str(investigation.get("trigger_source") or "unknown").strip() or "unknown"
        draft_customer_reply = str(investigation.get("draft_customer_reply") or "").strip()
        final_confirmation_requested_at = investigation.get("final_confirmation_requested_at")
        opened_at = investigation.get("opened_at") or _utc_now()
        updated_at = investigation.get("updated_at") or _utc_now()
        closed_at = investigation.get("closed_at")

        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                investigation_id,
                                ticket_id,
                                state,
                                trigger_reason,
                                trigger_source,
                                draft_customer_reply,
                                final_confirmation_requested_at,
                                opened_at,
                                updated_at,
                                closed_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (investigation_id) DO UPDATE SET
                                state = EXCLUDED.state,
                                trigger_reason = EXCLUDED.trigger_reason,
                                trigger_source = EXCLUDED.trigger_source,
                                draft_customer_reply = EXCLUDED.draft_customer_reply,
                                final_confirmation_requested_at = EXCLUDED.final_confirmation_requested_at,
                                updated_at = EXCLUDED.updated_at,
                                closed_at = EXCLUDED.closed_at
                            """
                        ).format(self._table("support_ticket_investigations")),
                        (
                            investigation_id,
                            ticket_id,
                            state,
                            trigger_reason,
                            trigger_source,
                            draft_customer_reply,
                            final_confirmation_requested_at,
                            opened_at,
                            updated_at,
                            closed_at,
                        ),
                    )

                    for message in new_messages or []:
                        content = str(message.get("content") or "").strip()
                        if not content:
                            continue
                        cur.execute(
                            sql.SQL(
                                """
                                INSERT INTO {} (
                                    message_id,
                                    investigation_id,
                                    role,
                                    content,
                                    created_at,
                                    meta
                                )
                                VALUES (%s, %s, %s, %s, %s, %s)
                                """
                            ).format(self._table("support_ticket_investigation_messages")),
                            (
                                str(message.get("id") or f"{investigation_id}-{uuid4().hex[:8]}"),
                                investigation_id,
                                _normalize_investigation_role(message.get("role")),
                                content,
                                message.get("created_at") or updated_at,
                                Json(message.get("meta")) if isinstance(message.get("meta"), dict) else None,
                            ),
                        )

        self._run_with_connection_retry("save_investigation", _operation)

    def record_event(self, ticket_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (ticket_id, event_type, payload)
                            VALUES (%s, %s, %s)
                            """
                        ).format(self._table("support_ticket_events")),
                        (ticket_id, event_type, Json(payload)),
                    )

        self._run_with_connection_retry("record_event", _operation)

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 20)
        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT ticket_id, event_type, payload, created_at
                        FROM {}
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_ticket_events")),
                    (safe_limit,),
                )
                rows = cur.fetchall()
            events: list[dict[str, Any]] = []
            for row in rows:
                events.append(
                    {
                        "ticket_id": str(row[0]) if row[0] is not None else None,
                        "event_type": str(row[1]),
                        "payload": row[2] if isinstance(row[2], dict) else {},
                        "created_at": _to_iso(row[3]),
                    }
                )
            return events

        return self._run_with_connection_retry("list_events", _operation)

    def list_ticket_events(self, ticket_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT ticket_id, event_type, payload, created_at
                        FROM {}
                        WHERE ticket_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_ticket_events")),
                    (ticket_id, safe_limit),
                )
                rows = cur.fetchall()
            events: list[dict[str, Any]] = []
            for row in rows:
                events.append(
                    {
                        "ticket_id": str(row[0]) if row[0] is not None else None,
                        "event_type": str(row[1]),
                        "payload": row[2] if isinstance(row[2], dict) else {},
                        "created_at": _to_iso(row[3]),
                    }
                )
            return events

        return self._run_with_connection_retry("list_ticket_events", _operation)

    def record_ticket_agent_event(
        self,
        ticket_id: str,
        message_id: str | None,
        run_id: str,
        agent_name: str,
        phase: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                ticket_id,
                                message_id,
                                run_id,
                                agent_name,
                                phase,
                                event_type,
                                payload
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """
                        ).format(self._table("support_ticket_agent_events")),
                        (
                            ticket_id,
                            str(message_id or "").strip() or None,
                            str(run_id).strip(),
                            str(agent_name).strip(),
                            str(phase).strip(),
                            str(event_type).strip(),
                            Json(payload),
                        ),
                    )

        self._run_with_connection_retry("record_ticket_agent_event", _operation)

    def list_ticket_agent_events(self, ticket_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)

        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT ticket_id, message_id, run_id, agent_name, phase, event_type, payload, created_at
                        FROM {}
                        WHERE ticket_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_ticket_agent_events")),
                    (ticket_id, safe_limit),
                )
                rows = cur.fetchall()
            return [
                {
                    "ticket_id": str(row[0]) if row[0] is not None else None,
                    "message_id": str(row[1]) if row[1] is not None else None,
                    "run_id": str(row[2]),
                    "agent_name": str(row[3]),
                    "phase": str(row[4]),
                    "event_type": str(row[5]),
                    "payload": row[6] if isinstance(row[6], dict) else {},
                    "created_at": _to_iso(row[7]),
                }
                for row in rows
            ]

        return self._run_with_connection_retry("list_ticket_agent_events", _operation)

    def save_billing_ticket(self, billing_ticket: dict[str, Any]) -> None:
        billing_ticket_id = str(billing_ticket.get("billing_ticket_id") or "").strip()
        if not billing_ticket_id:
            raise ValueError("billing_ticket_id is required")
        client_ticket_id = str(billing_ticket.get("client_ticket_id") or "").strip()
        if not client_ticket_id:
            raise ValueError("client_ticket_id is required")

        created_at = billing_ticket.get("created_at") or _utc_now()
        updated_at = billing_ticket.get("updated_at") or created_at

        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                billing_ticket_id, client_ticket_id, source, external_id,
                                created_by, title, question, route, scope_label,
                                route_family, execution_action, tooling_profile,
                                route_reason, route_confidence, matched_signals,
                                automation_status, missing_fields, collected_fields,
                                customer_reply, internal_email_payload,
                                internal_email_send_status, internal_email_send_reason,
                                semantic_intent, automation_eligibility, policy_decision,
                                not_automated_reason, risk_flags, evidence_spans,
                                router_source, created_at, updated_at
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (billing_ticket_id) DO UPDATE SET
                                client_ticket_id = EXCLUDED.client_ticket_id,
                                source = EXCLUDED.source,
                                external_id = EXCLUDED.external_id,
                                created_by = EXCLUDED.created_by,
                                title = EXCLUDED.title,
                                question = EXCLUDED.question,
                                route = EXCLUDED.route,
                                scope_label = EXCLUDED.scope_label,
                                route_family = EXCLUDED.route_family,
                                execution_action = EXCLUDED.execution_action,
                                tooling_profile = EXCLUDED.tooling_profile,
                                route_reason = EXCLUDED.route_reason,
                                route_confidence = EXCLUDED.route_confidence,
                                matched_signals = EXCLUDED.matched_signals,
                                automation_status = EXCLUDED.automation_status,
                                missing_fields = EXCLUDED.missing_fields,
                                collected_fields = EXCLUDED.collected_fields,
                                customer_reply = EXCLUDED.customer_reply,
                                internal_email_payload = EXCLUDED.internal_email_payload,
                                internal_email_send_status = EXCLUDED.internal_email_send_status,
                                internal_email_send_reason = EXCLUDED.internal_email_send_reason,
                                semantic_intent = EXCLUDED.semantic_intent,
                                automation_eligibility = EXCLUDED.automation_eligibility,
                                policy_decision = EXCLUDED.policy_decision,
                                not_automated_reason = EXCLUDED.not_automated_reason,
                                risk_flags = EXCLUDED.risk_flags,
                                evidence_spans = EXCLUDED.evidence_spans,
                                router_source = EXCLUDED.router_source,
                                updated_at = EXCLUDED.updated_at
                            """
                        ).format(self._table("support_billing_tickets")),
                        (
                            billing_ticket_id,
                            client_ticket_id,
                            str(billing_ticket.get("source") or "").strip(),
                            str(billing_ticket.get("external_id") or "").strip() or None,
                            str(billing_ticket.get("created_by") or "").strip() or None,
                            str(billing_ticket.get("title") or "").strip(),
                            str(billing_ticket.get("question") or "").strip(),
                            str(billing_ticket.get("route") or "").strip() or None,
                            str(billing_ticket.get("scope_label") or "").strip() or None,
                            str(billing_ticket.get("route_family") or "").strip() or None,
                            str(billing_ticket.get("execution_action") or "").strip() or None,
                            str(billing_ticket.get("tooling_profile") or "").strip() or None,
                            str(billing_ticket.get("route_reason") or "").strip() or None,
                            float(billing_ticket["route_confidence"]) if billing_ticket.get("route_confidence") is not None else None,
                            Json(billing_ticket.get("matched_signals")) if isinstance(billing_ticket.get("matched_signals"), list) else None,
                            str(billing_ticket.get("automation_status") or "").strip(),
                            Json(billing_ticket.get("missing_fields")) if isinstance(billing_ticket.get("missing_fields"), list) else Json([]),
                            Json(billing_ticket.get("collected_fields")) if isinstance(billing_ticket.get("collected_fields"), dict) else Json({}),
                            str(billing_ticket.get("customer_reply") or "").strip() or None,
                            Json(billing_ticket.get("internal_email_payload")) if isinstance(billing_ticket.get("internal_email_payload"), dict) else None,
                            str(billing_ticket.get("internal_email_send_status") or "").strip() or None,
                            str(billing_ticket.get("internal_email_send_reason") or "").strip() or None,
                            str(billing_ticket.get("semantic_intent") or "").strip() or None,
                            str(billing_ticket.get("automation_eligibility") or "").strip() or None,
                            str(billing_ticket.get("policy_decision") or "").strip() or None,
                            str(billing_ticket.get("not_automated_reason") or "").strip() or None,
                            Json(billing_ticket.get("risk_flags")) if isinstance(billing_ticket.get("risk_flags"), list) else Json([]),
                            Json(billing_ticket.get("evidence_spans")) if isinstance(billing_ticket.get("evidence_spans"), list) else Json([]),
                            str(billing_ticket.get("router_source") or "").strip() or None,
                            created_at,
                            updated_at,
                        ),
                    )

        self._run_with_connection_retry("save_billing_ticket", _operation)

    def get_billing_ticket(self, billing_ticket_id: str) -> dict[str, Any] | None:
        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE billing_ticket_id = %s"
                    ).format(self._table("support_billing_tickets")),
                    (str(billing_ticket_id).strip(),),
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                col_names = [desc[0] for desc in cur.description]
                return dict(zip(col_names, rows[0]))

        return self._run_with_connection_retry("get_billing_ticket", _operation)

    def get_billing_ticket_by_client_ticket_id(self, client_ticket_id: str) -> dict[str, Any] | None:
        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE client_ticket_id = %s"
                    ).format(self._table("support_billing_tickets")),
                    (str(client_ticket_id).strip(),),
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                col_names = [desc[0] for desc in cur.description]
                return dict(zip(col_names, rows[0]))

        return self._run_with_connection_retry("get_billing_ticket_by_client_ticket_id", _operation)

    def list_billing_tickets(
        self,
        limit: int = 30,
        review_status: str | None = None,
        offset: int = 0,
        automation_filter: str | None = None,
        route_errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 30)
        safe_offset = _safe_non_negative_int(offset, 0)
        normalized_review_status = str(review_status).strip() if review_status else None
        normalized_automation_filter = str(automation_filter or "").strip()

        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                where_sql, params = self._billing_ticket_filter_sql(
                    review_status=normalized_review_status,
                    automation_filter=normalized_automation_filter,
                    route_errors_only=route_errors_only,
                )
                query = sql.SQL(
                    """
                    SELECT bt.* FROM {} bt
                    {}
                    ORDER BY bt.created_at DESC
                    LIMIT %s OFFSET %s
                    """
                ).format(self._table("support_billing_tickets"), where_sql)
                cur.execute(query, (*params, safe_limit, safe_offset))
                col_names = [desc[0] for desc in cur.description]
                return [dict(zip(col_names, row)) for row in cur.fetchall()]

        return self._run_with_connection_retry("list_billing_tickets", _operation)

    def count_billing_tickets(
        self,
        review_status: str | None = None,
        automation_filter: str | None = None,
        route_errors_only: bool = False,
    ) -> int:
        normalized_review_status = str(review_status).strip() if review_status else None
        normalized_automation_filter = str(automation_filter or "").strip()

        def _operation(conn: psycopg.Connection[Any]) -> int:
            with conn.cursor() as cur:
                where_sql, params = self._billing_ticket_filter_sql(
                    review_status=normalized_review_status,
                    automation_filter=normalized_automation_filter,
                    route_errors_only=route_errors_only,
                )
                query = sql.SQL("SELECT COUNT(*) FROM {} bt {}").format(
                    self._table("support_billing_tickets"), where_sql
                )
                cur.execute(query, params)
                row = cur.fetchone()
                return int(row[0]) if row else 0

        return self._run_with_connection_retry("count_billing_tickets", _operation)

    def _billing_ticket_filter_sql(
        self,
        *,
        review_status: str | None,
        automation_filter: str,
        route_errors_only: bool,
    ) -> tuple[sql.SQL, tuple[Any, ...]]:
        clauses: list[sql.SQL] = []
        params: list[Any] = []
        if review_status:
            clauses.append(sql.SQL("bt.route_review_status = %s"))
            params.append(review_status)
        if automation_filter == "automation":
            clauses.append(sql.SQL("bt.automation_status IN ('automation', 'automated')"))
        elif automation_filter == "not_automated":
            clauses.append(sql.SQL("bt.automation_status NOT IN ('automation', 'automated')"))
        if route_errors_only:
            clauses.append(
                sql.SQL(
                    """
                    (
                        EXISTS (
                            SELECT 1 FROM {} brc
                            WHERE brc.billing_ticket_id = bt.billing_ticket_id
                        )
                        OR COALESCE(bt.route_confidence, 1.0) < 0.6
                    )
                    """
                ).format(self._table("support_billing_route_corrections"))
            )
        if not clauses:
            return sql.SQL(""), tuple()
        return sql.SQL("WHERE ") + sql.SQL(" AND ").join(clauses), tuple(params)

    def save_billing_response_token(self, token: dict[str, Any]) -> None:
        token_hash = str(token.get("token_hash") or "").strip()
        if not token_hash:
            raise ValueError("token_hash is required")
        billing_ticket_id = str(token.get("billing_ticket_id") or "").strip()
        if not billing_ticket_id:
            raise ValueError("billing_ticket_id is required")
        created_at = token.get("created_at") or _utc_now()
        used_at = token.get("used_at")

        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (token_hash, billing_ticket_id, created_at, used_at)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (token_hash) DO NOTHING
                            """
                        ).format(self._table("support_billing_response_tokens")),
                        (token_hash, billing_ticket_id, created_at, used_at),
                    )

        self._run_with_connection_retry("save_billing_response_token", _operation)

    def get_billing_response_token(self, token_hash: str) -> dict[str, Any] | None:
        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT * FROM {} WHERE token_hash = %s").format(
                        self._table("support_billing_response_tokens")
                    ),
                    (str(token_hash).strip(),),
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                col_names = [desc[0] for desc in cur.description]
                return dict(zip(col_names, rows[0]))

        return self._run_with_connection_retry("get_billing_response_token", _operation)

    def mark_billing_response_token_used(self, token_hash: str, used_at: str) -> bool:
        def _operation(conn: psycopg.Connection[Any]) -> bool:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET used_at = %s
                            WHERE token_hash = %s AND used_at IS NULL
                            """
                        ).format(self._table("support_billing_response_tokens")),
                        (used_at, str(token_hash).strip()),
                    )
                    return cur.rowcount == 1

        return self._run_with_connection_retry("mark_billing_response_token_used", _operation)

    def save_billing_route_correction(self, correction: dict[str, Any]) -> None:
        billing_ticket_id = str(correction.get("billing_ticket_id") or "").strip()
        if not billing_ticket_id:
            raise ValueError("billing_ticket_id is required")
        created_at = correction.get("created_at") or _utc_now()
        updated_at = correction.get("updated_at") or created_at

        def _operation(conn: psycopg.Connection[Any]) -> None:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} AS corrections (
                                billing_ticket_id, client_ticket_id,
                                original_scope_label, original_route_family,
                                original_execution_action, original_tooling_profile,
                                original_route_reason, original_route_confidence,
                                corrected_scope_label, corrected_route_family,
                                corrected_execution_action, corrected_tooling_profile,
                                first_corrected_scope_label, first_corrected_route_family,
                                first_corrected_execution_action, first_corrected_tooling_profile,
                                corrector, correction_count, created_at, updated_at
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (billing_ticket_id) DO UPDATE SET
                                client_ticket_id = EXCLUDED.client_ticket_id,
                                original_scope_label = corrections.original_scope_label,
                                original_route_family = corrections.original_route_family,
                                original_execution_action = corrections.original_execution_action,
                                original_tooling_profile = corrections.original_tooling_profile,
                                original_route_reason = corrections.original_route_reason,
                                original_route_confidence = corrections.original_route_confidence,
                                corrected_scope_label = EXCLUDED.corrected_scope_label,
                                corrected_route_family = EXCLUDED.corrected_route_family,
                                corrected_execution_action = EXCLUDED.corrected_execution_action,
                                corrected_tooling_profile = EXCLUDED.corrected_tooling_profile,
                                first_corrected_scope_label = corrections.first_corrected_scope_label,
                                first_corrected_route_family = corrections.first_corrected_route_family,
                                first_corrected_execution_action = corrections.first_corrected_execution_action,
                                first_corrected_tooling_profile = corrections.first_corrected_tooling_profile,
                                corrector = EXCLUDED.corrector,
                                correction_count = corrections.correction_count + 1,
                                created_at = corrections.created_at,
                                updated_at = EXCLUDED.updated_at
                            """
                        ).format(self._table("support_billing_route_corrections")),
                        (
                            billing_ticket_id,
                            str(correction.get("client_ticket_id") or "").strip(),
                            str(correction.get("original_scope_label") or "").strip() or None,
                            str(correction.get("original_route_family") or "").strip() or None,
                            str(correction.get("original_execution_action") or "").strip() or None,
                            str(correction.get("original_tooling_profile") or "").strip() or None,
                            str(correction.get("original_route_reason") or "").strip() or None,
                            float(correction["original_route_confidence"])
                            if correction.get("original_route_confidence") is not None
                            else None,
                            str(correction.get("corrected_scope_label") or "").strip(),
                            str(correction.get("corrected_route_family") or "").strip(),
                            str(correction.get("corrected_execution_action") or "").strip(),
                            str(correction.get("corrected_tooling_profile") or "").strip(),
                            str(correction.get("first_corrected_scope_label") or "").strip(),
                            str(correction.get("first_corrected_route_family") or "").strip(),
                            str(correction.get("first_corrected_execution_action") or "").strip(),
                            str(correction.get("first_corrected_tooling_profile") or "").strip(),
                            str(correction.get("corrector") or "").strip() or None,
                            int(correction.get("correction_count") or 1),
                            created_at,
                            updated_at,
                        ),
                    )

        self._run_with_connection_retry("save_billing_route_correction", _operation)

    def get_billing_route_correction(self, billing_ticket_id: str) -> dict[str, Any] | None:
        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT * FROM {} WHERE billing_ticket_id = %s").format(
                        self._table("support_billing_route_corrections")
                    ),
                    (str(billing_ticket_id).strip(),),
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                col_names = [desc[0] for desc in cur.description]
                return dict(zip(col_names, rows[0]))

        return self._run_with_connection_retry("get_billing_route_correction", _operation)

    def get_billing_route_corrections_for_tickets(
        self, billing_ticket_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        normalized_ids = [
            str(raw_id or "").strip() for raw_id in billing_ticket_ids if str(raw_id or "").strip()
        ]
        if not normalized_ids:
            return {}

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT * FROM {} WHERE billing_ticket_id = ANY(%s)").format(
                        self._table("support_billing_route_corrections")
                    ),
                    (normalized_ids,),
                )
                rows = cur.fetchall()
                if not rows:
                    return {}
                col_names = [desc[0] for desc in cur.description]
                result: dict[str, dict[str, Any]] = {}
                for row in rows:
                    record = dict(zip(col_names, row))
                    billing_ticket_id = str(record.get("billing_ticket_id") or "").strip()
                    if billing_ticket_id:
                        result[billing_ticket_id] = record
                return result

        return self._run_with_connection_retry(
            "get_billing_route_corrections_for_tickets", _operation
        )

    def list_billing_route_corrections(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)

        def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT * FROM {}
                        ORDER BY updated_at DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_billing_route_corrections")),
                    (safe_limit,),
                )
                col_names = [desc[0] for desc in cur.description]
                return [dict(zip(col_names, row)) for row in cur.fetchall()]

        return self._run_with_connection_retry("list_billing_route_corrections", _operation)

    def apply_billing_route_correction(
        self,
        *,
        billing_ticket_id: str,
        active_route: dict[str, Any],
        correction: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_id = str(billing_ticket_id or "").strip()
        if not normalized_id:
            raise ValueError("billing_ticket_id is required")
        updated_at = active_route.get("updated_at") or correction.get("updated_at") or _utc_now()

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL("SELECT * FROM {} WHERE billing_ticket_id = %s FOR UPDATE").format(
                            self._table("support_billing_tickets")
                        ),
                        (normalized_id,),
                    )
                    ticket_rows = cur.fetchall()
                    if not ticket_rows:
                        raise KeyError(normalized_id)
                    cur.execute(
                        sql.SQL("SELECT * FROM {} WHERE billing_ticket_id = %s FOR UPDATE").format(
                            self._table("support_billing_route_corrections")
                        ),
                        (normalized_id,),
                    )
                    existing_rows = cur.fetchall()
                    existing: dict[str, Any] | None = None
                    if existing_rows:
                        col_names = [desc[0] for desc in cur.description]
                        existing = dict(zip(col_names, existing_rows[0]))

                    created_at = correction.get("created_at") or updated_at
                    if existing is not None:
                        persisted_count = int(existing.get("correction_count") or 0) + 1
                        for key in (
                            "original_scope_label",
                            "original_route_family",
                            "original_execution_action",
                            "original_tooling_profile",
                            "original_route_reason",
                            "original_route_confidence",
                            "first_corrected_scope_label",
                            "first_corrected_route_family",
                            "first_corrected_execution_action",
                            "first_corrected_tooling_profile",
                        ):
                            correction[key] = existing.get(key)
                        created_at = existing.get("created_at") or created_at
                    else:
                        persisted_count = 1

                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET route = %s,
                                scope_label = %s,
                                route_family = %s,
                                execution_action = %s,
                                tooling_profile = %s,
                                updated_at = %s
                            WHERE billing_ticket_id = %s
                            """
                        ).format(self._table("support_billing_tickets")),
                        (
                            str(active_route.get("route") or "").strip() or None,
                            str(active_route.get("scope_label") or "").strip() or None,
                            str(active_route.get("route_family") or "").strip() or None,
                            str(active_route.get("execution_action") or "").strip() or None,
                            str(active_route.get("tooling_profile") or "").strip() or None,
                            updated_at,
                            normalized_id,
                        ),
                    )

                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} AS corrections (
                                billing_ticket_id, client_ticket_id,
                                original_scope_label, original_route_family,
                                original_execution_action, original_tooling_profile,
                                original_route_reason, original_route_confidence,
                                corrected_scope_label, corrected_route_family,
                                corrected_execution_action, corrected_tooling_profile,
                                first_corrected_scope_label, first_corrected_route_family,
                                first_corrected_execution_action, first_corrected_tooling_profile,
                                corrector, correction_count, created_at, updated_at
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (billing_ticket_id) DO UPDATE SET
                                client_ticket_id = EXCLUDED.client_ticket_id,
                                original_scope_label = corrections.original_scope_label,
                                original_route_family = corrections.original_route_family,
                                original_execution_action = corrections.original_execution_action,
                                original_tooling_profile = corrections.original_tooling_profile,
                                original_route_reason = corrections.original_route_reason,
                                original_route_confidence = corrections.original_route_confidence,
                                corrected_scope_label = EXCLUDED.corrected_scope_label,
                                corrected_route_family = EXCLUDED.corrected_route_family,
                                corrected_execution_action = EXCLUDED.corrected_execution_action,
                                corrected_tooling_profile = EXCLUDED.corrected_tooling_profile,
                                first_corrected_scope_label = corrections.first_corrected_scope_label,
                                first_corrected_route_family = corrections.first_corrected_route_family,
                                first_corrected_execution_action = corrections.first_corrected_execution_action,
                                first_corrected_tooling_profile = corrections.first_corrected_tooling_profile,
                                corrector = EXCLUDED.corrector,
                                correction_count = EXCLUDED.correction_count,
                                created_at = corrections.created_at,
                                updated_at = EXCLUDED.updated_at
                            RETURNING *
                            """
                        ).format(self._table("support_billing_route_corrections")),
                        (
                            normalized_id,
                            str(correction.get("client_ticket_id") or "").strip(),
                            str(correction.get("original_scope_label") or "").strip() or None,
                            str(correction.get("original_route_family") or "").strip() or None,
                            str(correction.get("original_execution_action") or "").strip() or None,
                            str(correction.get("original_tooling_profile") or "").strip() or None,
                            str(correction.get("original_route_reason") or "").strip() or None,
                            float(correction["original_route_confidence"])
                            if correction.get("original_route_confidence") is not None
                            else None,
                            str(correction.get("corrected_scope_label") or "").strip(),
                            str(correction.get("corrected_route_family") or "").strip(),
                            str(correction.get("corrected_execution_action") or "").strip(),
                            str(correction.get("corrected_tooling_profile") or "").strip(),
                            str(correction.get("first_corrected_scope_label") or "").strip(),
                            str(correction.get("first_corrected_route_family") or "").strip(),
                            str(correction.get("first_corrected_execution_action") or "").strip(),
                            str(correction.get("first_corrected_tooling_profile") or "").strip(),
                            str(correction.get("corrector") or "").strip() or None,
                            persisted_count,
                            created_at,
                            updated_at,
                        ),
                    )
                    row = cur.fetchone()
                    col_names = [desc[0] for desc in cur.description]
                    return dict(zip(col_names, row)) if row else {}

        return self._run_with_connection_retry("apply_billing_route_correction", _operation)

    def mark_billing_route_reviewed(
        self,
        *,
        billing_ticket_id: str,
        review_status: str,
    ) -> dict[str, Any]:
        normalized_id = str(billing_ticket_id or "").strip()
        if not normalized_id:
            raise ValueError("billing_ticket_id is required")
        normalized_status = str(review_status or "pending").strip() or "pending"
        updated_at = _utc_now()

        def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET route_review_status = %s,
                                updated_at = %s
                            WHERE billing_ticket_id = %s
                            RETURNING *
                            """
                        ).format(self._table("support_billing_tickets")),
                        (normalized_status, updated_at, normalized_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise KeyError(normalized_id)
                    col_names = [desc[0] for desc in cur.description]
                    return dict(zip(col_names, row))

        return self._run_with_connection_retry("mark_billing_route_reviewed", _operation)


def create_ticket_repository() -> TicketRepository:
    dsn = (os.getenv("TICKET_DB_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("TICKET_DB_DSN is required")
    schema = (os.getenv("TICKET_DB_SCHEMA") or "supportportal").strip() or "supportportal"
    connect_timeout = _safe_positive_int(os.getenv("TICKET_DB_CONNECT_TIMEOUT"), 10)
    connect_retries = _safe_positive_int(os.getenv("TICKET_DB_CONNECT_RETRIES"), 0)
    connect_retry_delay_seconds = _safe_positive_float(
        os.getenv("TICKET_DB_CONNECT_RETRY_DELAY_SECONDS"),
        1.0,
    )
    pool_min_size = _safe_positive_int(os.getenv("TICKET_DB_POOL_MIN_SIZE"), 1)
    pool_max_size = _safe_positive_int(os.getenv("TICKET_DB_POOL_MAX_SIZE"), 8)
    pool_timeout_seconds = _safe_positive_float(os.getenv("TICKET_DB_POOL_TIMEOUT_SECONDS"), 15.0)
    pool_acquire_budget_seconds = _safe_positive_float(
        os.getenv("TICKET_DB_POOL_ACQUIRE_BUDGET_SECONDS"),
        20.0,
    )
    pool_max_lifetime_seconds = _safe_positive_float(
        os.getenv("TICKET_DB_POOL_MAX_LIFETIME_SECONDS"),
        1800.0,
    )
    pool_max_idle_seconds = _safe_positive_float(
        os.getenv("TICKET_DB_POOL_MAX_IDLE_SECONDS"),
        300.0,
    )
    application_name = str(os.getenv("TICKET_DB_APPLICATION_NAME") or "").strip() or None
    migration_dsn = (os.getenv("TICKET_DB_MIGRATION_DSN") or "").strip() or None
    return PostgresTicketRepository(
        dsn=dsn,
        schema=schema,
        connect_timeout=connect_timeout,
        connect_retries=connect_retries,
        connect_retry_delay_seconds=connect_retry_delay_seconds,
        use_connection_pool=True,
        pool_min_size=pool_min_size,
        pool_max_size=pool_max_size,
        pool_timeout_seconds=pool_timeout_seconds,
        pool_acquire_budget_seconds=pool_acquire_budget_seconds,
        pool_max_lifetime_seconds=pool_max_lifetime_seconds,
        pool_max_idle_seconds=pool_max_idle_seconds,
        application_name=application_name,
        migration_dsn=migration_dsn,
    )

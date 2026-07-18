from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol


ASSIGNMENT_PENDING = "pending"
ASSIGNMENT_ASSIGNED = "assigned"
ASSIGNMENT_RESOLVED = "resolved"
DEFAULT_SLA_HOURS = 3


class EngineerAssignmentRepository(Protocol):
    def get_engineer_case(
        self,
        engineer_case_id: str,
        *,
        include_client_messages: bool = True,
    ) -> dict[str, Any] | None: ...

    def list_engineer_case_headers(self) -> list[dict[str, Any]]: ...

    def list_workspace_accounts(self) -> list[dict[str, Any]]: ...

    def save_workspace_account(self, account: dict[str, Any]) -> dict[str, Any]: ...

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
    ) -> dict[str, Any] | None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class EngineerAssignmentService:
    def __init__(
        self,
        repository: EngineerAssignmentRepository,
        *,
        sla_hours: int = DEFAULT_SLA_HOURS,
        now_provider: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.repository = repository
        self.sla_hours = max(1, int(sla_hours))
        self.now_provider = now_provider
        self._dispatch_lock = threading.RLock()

    def dispatch_case(
        self,
        engineer_case_id: str,
        *,
        reason: str = "round_robin",
        actor: str = "assignment-service",
    ) -> dict[str, Any] | None:
        with self._dispatch_lock:
            engineer_case = self.repository.get_engineer_case(
                engineer_case_id,
                include_client_messages=False,
            )
            if engineer_case is None:
                return None
            if str(engineer_case.get("assignment_status") or "") == ASSIGNMENT_RESOLVED:
                return engineer_case

            now = self.now_provider().astimezone(timezone.utc)
            now_iso = now.isoformat()
            current_engineer_id = str(engineer_case.get("assigned_engineer_id") or "").strip()
            available = [
                account
                for account in self.repository.list_workspace_accounts()
                if str(account.get("role") or "").strip().lower() == "engineer"
                and bool(account.get("active", True))
                and str(account.get("availability") or "").strip().lower() == "available"
            ]
            candidates = [
                account
                for account in available
                if str(account.get("account_id") or "").strip() != current_engineer_id
            ]
            if not candidates and not current_engineer_id:
                candidates = available
            candidates.sort(
                key=lambda account: (
                    _parse_datetime(account.get("last_assigned_at")) or datetime.min.replace(tzinfo=timezone.utc),
                    str(account.get("account_id") or ""),
                )
            )

            expected_version = int(engineer_case.get("assignment_version") or 0)
            if not candidates:
                if (
                    str(engineer_case.get("assignment_status") or "") == ASSIGNMENT_PENDING
                    and not current_engineer_id
                ):
                    return engineer_case
                return self.repository.update_engineer_case_assignment(
                    engineer_case_id,
                    expected_version=expected_version,
                    assignment_status=ASSIGNMENT_PENDING,
                    assigned_engineer_id=None,
                    assigned_at=None,
                    sla_due_at=None,
                    reason="no_available_engineer",
                    updated_at=now_iso,
                    actor=actor,
                    event_type="engineer_case_dispatch_pending",
                    dispatch_status="pending",
                )

            selected = candidates[0]
            selected_engineer_id = str(selected.get("account_id") or "").strip()
            sla_due_at = (now + timedelta(hours=self.sla_hours)).isoformat()
            event_type = (
                "engineer_case_assigned"
                if not current_engineer_id
                else "engineer_case_reassigned"
            )
            if reason == "sla_expired":
                event_type = "engineer_case_sla_reassigned"
            elif reason == "engineer_unavailable":
                event_type = "engineer_case_availability_reassigned"
            updated = self.repository.update_engineer_case_assignment(
                engineer_case_id,
                expected_version=expected_version,
                assignment_status=ASSIGNMENT_ASSIGNED,
                assigned_engineer_id=selected_engineer_id,
                assigned_at=now_iso,
                sla_due_at=sla_due_at,
                reason=reason,
                updated_at=now_iso,
                actor=actor,
                event_type=event_type,
                dispatch_status="assigned",
            )
            if updated is None:
                return None
            selected = dict(selected)
            selected["last_assigned_at"] = now_iso
            selected["updated_at"] = now_iso
            self.repository.save_workspace_account(selected)
            return updated

    def dispatch_pending_cases(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for engineer_case in self.repository.list_engineer_case_headers():
            if str(engineer_case.get("assignment_status") or "") != ASSIGNMENT_PENDING:
                continue
            updated = self.dispatch_case(
                str(engineer_case.get("engineer_case_id") or ""),
                reason="round_robin",
            )
            if updated is not None:
                results.append(updated)
        return results

    def reassign_due_cases(self) -> list[dict[str, Any]]:
        now = self.now_provider().astimezone(timezone.utc)
        results: list[dict[str, Any]] = []
        for engineer_case in self.repository.list_engineer_case_headers():
            if str(engineer_case.get("assignment_status") or "") != ASSIGNMENT_ASSIGNED:
                continue
            sla_due_at = _parse_datetime(engineer_case.get("sla_due_at"))
            if sla_due_at is None or sla_due_at > now:
                continue
            updated = self.dispatch_case(
                str(engineer_case.get("engineer_case_id") or ""),
                reason="sla_expired",
            )
            if updated is not None:
                results.append(updated)
        return results

    def reassign_unavailable_cases(self) -> list[dict[str, Any]]:
        accounts = {
            str(account.get("account_id") or "").strip(): account
            for account in self.repository.list_workspace_accounts()
            if str(account.get("account_id") or "").strip()
        }
        results: list[dict[str, Any]] = []
        for engineer_case in self.repository.list_engineer_case_headers():
            if str(engineer_case.get("assignment_status") or "") != ASSIGNMENT_ASSIGNED:
                continue
            engineer_id = str(engineer_case.get("assigned_engineer_id") or "").strip()
            account = accounts.get(engineer_id)
            if (
                isinstance(account, dict)
                and bool(account.get("active", True))
                and str(account.get("availability") or "").strip().lower() == "available"
            ):
                continue
            updated = self.dispatch_case(
                str(engineer_case.get("engineer_case_id") or ""),
                reason="engineer_unavailable",
            )
            if updated is not None:
                results.append(updated)
        return results

    def resolve_closed_cases(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for engineer_case in self.repository.list_engineer_case_headers():
            if str(engineer_case.get("assignment_status") or "") == ASSIGNMENT_RESOLVED:
                continue
            legacy_status = str(engineer_case.get("status") or "").strip().lower()
            if legacy_status != "resolved" and engineer_case.get("closed_at") is None:
                continue
            updated = self.resolve_case(
                str(engineer_case.get("engineer_case_id") or ""),
                actor="assignment-service",
                reason="case_closed_reconciliation",
            )
            if updated is not None:
                results.append(updated)
        return results

    def reassign_unavailable_engineer(self, engineer_id: str) -> list[dict[str, Any]]:
        normalized_engineer_id = str(engineer_id or "").strip()
        results: list[dict[str, Any]] = []
        for engineer_case in self.repository.list_engineer_case_headers():
            if str(engineer_case.get("assignment_status") or "") != ASSIGNMENT_ASSIGNED:
                continue
            if str(engineer_case.get("assigned_engineer_id") or "").strip() != normalized_engineer_id:
                continue
            updated = self.dispatch_case(
                str(engineer_case.get("engineer_case_id") or ""),
                reason="engineer_unavailable",
            )
            if updated is not None:
                results.append(updated)
        return results

    def resolve_case(
        self,
        engineer_case_id: str,
        *,
        actor: str,
        reason: str = "final_approve",
    ) -> dict[str, Any] | None:
        engineer_case = self.repository.get_engineer_case(
            engineer_case_id,
            include_client_messages=False,
        )
        if engineer_case is None:
            return None
        now_iso = self.now_provider().astimezone(timezone.utc).isoformat()
        return self.repository.update_engineer_case_assignment(
            engineer_case_id,
            expected_version=int(engineer_case.get("assignment_version") or 0),
            assignment_status=ASSIGNMENT_RESOLVED,
            assigned_engineer_id=str(engineer_case.get("assigned_engineer_id") or "").strip() or None,
            assigned_at=None,
            sla_due_at=None,
            reason=reason,
            updated_at=now_iso,
            actor=actor,
            event_type="engineer_case_assignment_resolved",
            dispatch_status="resolved",
        )

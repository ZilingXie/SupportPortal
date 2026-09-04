"""Strictly read-only Workspace Admin projections for ECS Production."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from backend.services.account_admin import (
    account_automation_payload,
    environment_config_description,
)
from backend.services.agent_config import build_agent_config_payload
from backend.services.automation_ecs_dashboard_reader import safe_zendesk_source
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.llm_pricing import estimate_token_usage_cost_usd, model_pricing_payload
from backend.services.workspace_schedules import (
    WORKSPACE_SCHEDULE_TIMEZONE,
    minutes_to_time,
    on_schedule_engineer_ids,
)


PRODUCTION_SCHEMA = "supportportal_production"
PRODUCTION_NAMESPACE = "supportportal-production"
_ENV_KEY_RE = re.compile(r"[A-Z_][A-Z0-9_]*")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _json_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_audit_payload(value: Any) -> dict[str, Any]:
    source = _json_dict(value)
    result = {
        key: source[key]
        for key in (
            "actor",
            "reason",
            "classification_reason_code",
            "route_reason_code",
            "execution_reason_code",
            "previous_assignment_status",
            "assignment_status",
            "previous_assigned_engineer_id",
            "assigned_engineer_id",
            "assignment_version",
        )
        if key in source
        and (source[key] is None or isinstance(source[key], (str, bool, int, float)))
    }
    route = source.get("route_classification")
    if isinstance(route, dict) and isinstance(route.get("reason_code"), str):
        result["route_classification"] = {"reason_code": route["reason_code"]}
    return result


class EmptyAutomationEcsAdminReader:
    """Test-only reader used when the ECS runtime explicitly allows memory storage."""

    def accounts(self) -> dict[str, Any]:
        return {"accounts": []}

    def cases(self) -> dict[str, Any]:
        return {"cases": [], "assignment_status_filter": "all"}

    def metrics(self) -> dict[str, Any]:
        return {
            "client_tickets": {"total": 0, "not_automated": 0},
            "engineer_cases": {"total": 0, "pending": 0, "assigned": 0, "resolved": 0},
            "engineers": {"total": 0, "on_schedule": 0, "off_schedule": 0, "dispatch_eligible": 0},
            "billing": {"total": 0, "automation": 0, "not_automated": 0, "internal_email_failed": 0},
            "guardrail": {"rejected": 0},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def audit(self, *, limit: int) -> dict[str, Any]:
        del limit
        return {"events": []}

    def engineer_schedules(self) -> dict[str, Any]:
        return {"timezone": WORKSPACE_SCHEDULE_TIMEZONE, "engineers": []}

    def account_automation(self, **_: Any) -> dict[str, Any]:
        return {
            "processing_profile": "production",
            "metrics": {"total_account_cases": 0, "automated_cases": 0, "not_automated_cases": 0, "automation_rate": 0},
            "automation_subcategories": [],
            "cases": [],
            "page": 1,
            "page_size": 50,
            "total": 0,
            "token_usage_page_total": {
                "total_input_tokens": 0,
                "total_cached_input_tokens": 0,
                "total_output_tokens": 0,
                "total_embedding_tokens": 0,
                "cost_usd_available": True,
                "cost_usd_total": 0.0,
            },
            "model_pricing": model_pricing_payload(),
        }

    def agent_config(self) -> dict[str, Any]:
        return build_agent_config_payload([], [])

    def environment_config(self) -> dict[str, Any]:
        return {"names": [], "items": []}


class AutomationEcsAdminReader:
    """Read the Production Admin contract without importing the legacy application."""

    def __init__(self, settings: AutomationEcsSettings) -> None:
        if settings.environment != "production":
            raise RuntimeError("ECS Admin is available only in Production")
        if settings.db_schema != PRODUCTION_SCHEMA:
            raise RuntimeError(f"ECS Admin requires {PRODUCTION_SCHEMA}")
        if settings.job_namespace != PRODUCTION_NAMESPACE:
            raise RuntimeError(f"ECS Admin requires namespace {PRODUCTION_NAMESPACE}")
        if not settings.db_dsn:
            raise RuntimeError("ECS Admin requires AUTOMATION_DB_DSN")
        self.settings = settings
        self._schema = sql.Identifier(settings.db_schema)

    def _table(self, name: str) -> sql.Composed:
        return sql.SQL("{}.{}").format(self._schema, sql.Identifier(name))

    @contextmanager
    def _read_cursor(self) -> Iterator[psycopg.Cursor[dict[str, Any]]]:
        with psycopg.connect(self.settings.db_dsn, row_factory=dict_row) as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                yield cursor

    def _accounts(self, cursor: psycopg.Cursor[dict[str, Any]]) -> list[dict[str, Any]]:
        cursor.execute(
            sql.SQL(
                """
                SELECT account_id, email, display_name, role, active,
                       last_assigned_at, created_at, updated_at
                FROM {}
                ORDER BY LOWER(display_name), account_id
                """
            ).format(self._table("support_workspace_accounts"))
        )
        return [
            {
                "account_id": str(row["account_id"]),
                "email": str(row.get("email") or "").strip().lower() or None,
                "display_name": str(row.get("display_name") or row["account_id"]),
                "role": str(row.get("role") or "engineer"),
                "active": bool(row.get("active", True)),
                "last_assigned_at": _iso(row.get("last_assigned_at")),
                "created_at": _iso(row.get("created_at")),
                "updated_at": _iso(row.get("updated_at")),
            }
            for row in cursor.fetchall()
        ]

    def accounts(self) -> dict[str, Any]:
        with self._read_cursor() as cursor:
            return {"accounts": self._accounts(cursor)}

    def _case_rows(self, cursor: psycopg.Cursor[dict[str, Any]]) -> list[dict[str, Any]]:
        cursor.execute(
            sql.SQL(
                """
                SELECT engineer_case.engineer_case_id, engineer_case.client_ticket_id,
                       engineer_case.case_sequence, engineer_case.title, engineer_case.status,
                       engineer_case.trigger_source, engineer_case.trigger_reason,
                       engineer_case.opened_at, engineer_case.updated_at, engineer_case.closed_at,
                       engineer_case.assigned_engineer_id, engineer_case.assignment_status,
                       engineer_case.assigned_at, engineer_case.sla_due_at,
                       engineer_case.assignment_attempt_count, engineer_case.previous_assignees,
                       engineer_case.last_assignment_reason, engineer_case.dispatch_status,
                       engineer_case.assignment_updated_at, engineer_case.assignment_version,
                       ticket.customer_id, ticket.requester, ticket.subject,
                       ticket.status AS client_status, ticket.created_at AS ticket_created_at,
                       ticket.updated_at AS ticket_updated_at
                FROM {} AS engineer_case
                JOIN {} AS ticket ON ticket.ticket_id=engineer_case.client_ticket_id
                ORDER BY COALESCE(engineer_case.assignment_updated_at, engineer_case.updated_at) DESC,
                         engineer_case.engineer_case_id
                """
            ).format(
                self._table("support_engineer_cases"),
                self._table("support_tickets"),
            )
        )
        return list(cursor.fetchall())

    @staticmethod
    def _case_payload(row: dict[str, Any]) -> dict[str, Any]:
        case_id = str(row.get("engineer_case_id") or "")
        client_ticket_id = str(row.get("client_ticket_id") or "")
        title = str(row.get("title") or row.get("subject") or "Engineer case")
        previous_assignees = row.get("previous_assignees")
        return {
            "engineer_case_id": case_id,
            "ticket_id": case_id,
            "client_ticket_id": client_ticket_id,
            "client_ticket_ref": {
                "ticket_id": client_ticket_id,
                "subject": str(row.get("subject") or ""),
                "status": str(row.get("client_status") or "open"),
            },
            "case_sequence": int(row.get("case_sequence") or 0),
            "title": title,
            "subject": title,
            "status": str(row.get("status") or "open"),
            "client_status": str(row.get("client_status") or "open"),
            "assignment_status": str(row.get("assignment_status") or "pending"),
            "assigned_engineer_id": str(row.get("assigned_engineer_id") or "").strip() or None,
            "assigned_at": _iso(row.get("assigned_at")),
            "sla_due_at": _iso(row.get("sla_due_at")),
            "assignment_attempt_count": int(row.get("assignment_attempt_count") or 0),
            "previous_assignees": list(previous_assignees) if isinstance(previous_assignees, list) else [],
            "last_assignment_reason": str(row.get("last_assignment_reason") or "").strip() or None,
            "dispatch_status": str(row.get("dispatch_status") or "pending"),
            "assignment_updated_at": _iso(row.get("assignment_updated_at")),
            "assignment_version": int(row.get("assignment_version") or 0),
            "trigger_source": str(row.get("trigger_source") or ""),
            "trigger_reason": str(row.get("trigger_reason") or ""),
            "requester": str(row.get("requester") or ""),
            "customer_id": str(row.get("customer_id") or ""),
            "created_at": _iso(row.get("opened_at")),
            "opened_at": _iso(row.get("opened_at")),
            "updated_at": _iso(row.get("updated_at")),
            "closed_at": _iso(row.get("closed_at")),
        }

    def cases(self) -> dict[str, Any]:
        with self._read_cursor() as cursor:
            return {
                "cases": [self._case_payload(row) for row in self._case_rows(cursor)],
                "assignment_status_filter": "all",
            }

    def _account_case_rows(self, cursor: psycopg.Cursor[dict[str, Any]]) -> list[dict[str, Any]]:
        cursor.execute(
            sql.SQL(
                """
                SELECT account_case.account_case_id, account_case.billing_ticket_id,
                       account_case.client_ticket_id, account_case.processing_profile,
                       account_case.zendesk_ticket_id, account_case.source, account_case.title,
                       account_case.route, account_case.scope_label, account_case.route_family,
                       account_case.execution_action, account_case.automation_status,
                       account_case.internal_email_send_status,
                       account_case.execution_reason_code, account_case.category,
                       account_case.subcategory, account_case.route_status,
                       account_case.automation_handler, account_case.route_classification,
                       account_case.created_at, account_case.updated_at
                FROM {} AS automation_case
                JOIN {} AS account_case
                  ON account_case.zendesk_ticket_id=automation_case.zendesk_ticket_id
                 AND account_case.processing_profile='production'
                WHERE automation_case.namespace=%s
                ORDER BY account_case.created_at DESC, account_case.account_case_id
                """
            ).format(
                self._table("automation_cases"),
                self._table("support_account_cases"),
            ),
            (self.settings.job_namespace,),
        )
        rows = list(cursor.fetchall())
        for row in rows:
            ticket_id = str(row.get("zendesk_ticket_id") or "")
            row["source"] = safe_zendesk_source(row.get("source"), ticket_id) or (
                f"https://agoraio.zendesk.com/agent/tickets/{ticket_id}"
                if ticket_id.isdigit()
                else "production"
            )
        return rows

    def metrics(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._read_cursor() as cursor:
            cases = self._case_rows(cursor)
            accounts = self._accounts(cursor)
            cursor.execute(sql.SQL("SELECT status FROM {}").format(self._table("support_tickets")))
            ticket_statuses = [str(row.get("status") or "").lower() for row in cursor.fetchall()]
            schedules = self._schedule_rows(cursor)
            account_cases = self._account_case_rows(cursor)
            cursor.execute(
                sql.SQL("SELECT event_type FROM {}").format(
                    self._table("support_engineer_case_events")
                )
            )
            event_types = [str(row.get("event_type") or "").lower() for row in cursor.fetchall()]

        assignment_counts = {key: 0 for key in ("pending", "assigned", "resolved")}
        first_assignment_seconds: list[float] = []
        resolution_seconds: list[float] = []
        overdue = 0
        for row in cases:
            status = str(row.get("assignment_status") or "pending")
            if status in assignment_counts:
                assignment_counts[status] += 1
            sla_due_at = row.get("sla_due_at")
            if status == "assigned" and isinstance(sla_due_at, datetime) and sla_due_at <= now:
                overdue += 1
            opened_at = row.get("opened_at")
            assigned_at = row.get("assigned_at")
            closed_at = row.get("closed_at")
            if isinstance(opened_at, datetime) and isinstance(assigned_at, datetime) and assigned_at >= opened_at:
                first_assignment_seconds.append((assigned_at - opened_at).total_seconds())
            if isinstance(opened_at, datetime) and isinstance(closed_at, datetime) and closed_at >= opened_at:
                resolution_seconds.append((closed_at - opened_at).total_seconds())

        active_engineers = {
            str(item["account_id"])
            for item in accounts
            if item["role"] == "engineer" and item["active"]
        }
        on_schedule = on_schedule_engineer_ids(schedules, now) & active_engineers
        automation_metrics = account_automation_payload(
            _AccountRows(account_cases), processing_profile="production", page_size=1
        )["metrics"]
        client_counts = {key: ticket_statuses.count(key) for key in ("open", "communicating", "escalated", "investigating", "resolved")}
        return {
            "client_tickets": {
                **client_counts,
                "total": len(ticket_statuses),
                "not_automated": int(automation_metrics["not_automated_cases"]),
            },
            "engineer_cases": {
                **assignment_counts,
                "total": len(cases),
                "sla_overdue": overdue,
                "dispatch_failed": sum(str(row.get("dispatch_status") or "") == "failed" for row in cases),
                "rollout_created": sum(str(row.get("trigger_source") or "") == "account_not_automated" for row in cases),
                "average_first_assignment_seconds": round(sum(first_assignment_seconds) / len(first_assignment_seconds), 2) if first_assignment_seconds else None,
                "average_resolution_seconds": round(sum(resolution_seconds) / len(resolution_seconds), 2) if resolution_seconds else None,
                "sla_reassigned": event_types.count("engineer_case_sla_reassigned"),
                "schedule_reassigned": event_types.count("engineer_case_schedule_reassigned"),
            },
            "engineers": {
                "total": sum(item["role"] == "engineer" for item in accounts),
                "on_schedule": len(on_schedule),
                "off_schedule": len(active_engineers - on_schedule),
                "dispatch_eligible": len(on_schedule),
            },
            "billing": {
                "total": int(automation_metrics["total_account_cases"]),
                "automation": int(automation_metrics["automated_cases"]),
                "not_automated": int(automation_metrics["not_automated_cases"]),
                "internal_email_failed": sum(
                    str(row.get("internal_email_send_status") or "").lower() == "failed"
                    for row in account_cases
                ),
            },
            "guardrail": {
                "rejected": sum("guardrail" in event and ("reject" in event or "fail" in event) for event in event_types)
            },
            "generated_at": now.isoformat(),
        }

    def audit(self, *, limit: int = 100) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._read_cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT event_type,actor_id,target_id,payload,created_at FROM {} "
                    "ORDER BY created_at DESC,id DESC LIMIT %s"
                ).format(self._table("support_workspace_audit_events")),
                (safe_limit,),
            )
            events = [
                {
                    "event_type": str(row.get("event_type") or ""),
                    "actor_id": str(row.get("actor_id") or "system"),
                    "target_id": str(row.get("target_id") or "").strip() or None,
                    "payload": _safe_audit_payload(row.get("payload")),
                    "created_at": _iso(row.get("created_at")),
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(
                sql.SQL(
                    "SELECT engineer_case_id,event_type,payload,created_at FROM {} "
                    "WHERE event_type ILIKE ANY(%s) ORDER BY created_at DESC,id DESC LIMIT %s"
                ).format(self._table("support_engineer_case_events")),
                (["%assign%", "%dispatch%", "%sla%"], safe_limit),
            )
            for row in cursor.fetchall():
                payload = _safe_audit_payload(row.get("payload"))
                events.append(
                    {
                        "event_type": str(row.get("event_type") or ""),
                        "actor_id": str(payload.get("actor") or "system"),
                        "target_id": str(row.get("engineer_case_id") or "") or None,
                        "payload": payload,
                        "created_at": _iso(row.get("created_at")),
                    }
                )
        events.sort(key=lambda event: str(event.get("created_at") or ""), reverse=True)
        return {"events": events[:safe_limit]}

    def _schedule_rows(self, cursor: psycopg.Cursor[dict[str, Any]]) -> list[dict[str, Any]]:
        cursor.execute(
            sql.SQL(
                "SELECT engineer_id,weekday,start_minute,end_minute,timezone,updated_by,updated_at "
                "FROM {} ORDER BY engineer_id,weekday"
            ).format(self._table("support_engineer_schedules"))
        )
        return list(cursor.fetchall())

    def engineer_schedules(self) -> dict[str, Any]:
        with self._read_cursor() as cursor:
            schedules = self._schedule_rows(cursor)
            accounts = self._accounts(cursor)
        on_schedule = on_schedule_engineer_ids(schedules)
        shifts: dict[str, list[dict[str, Any]]] = {}
        for item in schedules:
            shifts.setdefault(str(item["engineer_id"]), []).append(
                {
                    "weekday": int(item["weekday"]),
                    "start": minutes_to_time(int(item["start_minute"])),
                    "end": minutes_to_time(int(item["end_minute"])),
                }
            )
        engineers = []
        for account in accounts:
            if account["role"] != "engineer" or not account["active"]:
                continue
            engineer_id = str(account["account_id"])
            engineers.append(
                {
                    **account,
                    "is_on_schedule_now": engineer_id in on_schedule,
                    "shifts": shifts.get(engineer_id, []),
                }
            )
        return {"timezone": WORKSPACE_SCHEDULE_TIMEZONE, "engineers": engineers}

    def _usage_summaries(
        self,
        cursor: psycopg.Cursor[dict[str, Any]],
        billing_ticket_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not billing_ticket_ids:
            return {}
        cursor.execute(
            sql.SQL(
                """
                SELECT billing_ticket_id,stage,provider,model,prompt_tokens,
                       completion_tokens,cached_input_tokens,reasoning_tokens
                FROM {}
                WHERE billing_ticket_id=ANY(%s)
                ORDER BY created_at,id
                """
            ).format(self._table("support_account_case_llm_usage")),
            (billing_ticket_ids,),
        )
        grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in billing_ticket_ids}
        for row in cursor.fetchall():
            key = str(row.get("billing_ticket_id") or "")
            if key in grouped:
                grouped[key].append(row)
        return {key: _automation_usage(rows) for key, rows in grouped.items()}

    def account_automation(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        route_status: str | None = None,
        category: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> dict[str, Any]:
        with self._read_cursor() as cursor:
            rows = self._account_case_rows(cursor)
            payload = account_automation_payload(
                _AccountRows(rows),
                page=page,
                page_size=page_size,
                route_status=route_status,
                category=category,
                created_from=created_from,
                created_to=created_to,
                processing_profile="production",
            )
            page_cases = list(payload.get("cases") or [])
            billing_ids = [str(item.get("billing_ticket_id") or "") for item in page_cases]
            summaries = self._usage_summaries(cursor, [item for item in billing_ids if item])

        page_total = {
            "total_input_tokens": 0,
            "total_cached_input_tokens": 0,
            "total_output_tokens": 0,
            "total_embedding_tokens": 0,
            "cost_usd_available": True,
            "cost_usd_total": 0.0,
        }
        for item in page_cases:
            automation = summaries.get(str(item.get("billing_ticket_id") or ""), _automation_usage([]))
            usage = {
                "available": True,
                "error_reason": None,
                "total_input_tokens": automation["total_input_tokens"],
                "total_cached_input_tokens": automation["total_cached_input_tokens"],
                "total_output_tokens": automation["total_output_tokens"],
                "total_embedding_tokens": 0,
                "token_by_model": automation["token_by_model"],
                "sources": {
                    "rag": {
                        "available": False,
                        "error_reason": "RAG token usage is unavailable in ECS Admin",
                        "total_input_tokens": 0,
                        "total_cached_input_tokens": 0,
                        "total_output_tokens": 0,
                        "total_embedding_tokens": 0,
                        "stage_totals": {},
                    },
                    "automation": automation,
                },
            }
            usage["cost_usd"] = estimate_token_usage_cost_usd(usage)
            item["token_usage"] = usage
            for key in ("total_input_tokens", "total_cached_input_tokens", "total_output_tokens"):
                page_total[key] += int(usage[key])
            if usage["cost_usd"]["available"]:
                page_total["cost_usd_total"] += float(usage["cost_usd"]["total_usd"] or 0)
            else:
                page_total["cost_usd_available"] = False
        payload["token_usage_page_total"] = page_total
        payload["model_pricing"] = model_pricing_payload()
        return payload

    def _personas(self, cursor: psycopg.Cursor[dict[str, Any]]) -> list[dict[str, Any]]:
        cursor.execute(
            sql.SQL(
                "SELECT persona_key,display_name,enabled,published_version,created_at,updated_at "
                "FROM {} ORDER BY persona_key"
            ).format(self._table("support_account_personas"))
        )
        personas = [dict(row) for row in cursor.fetchall()]
        for persona in personas:
            persona["persona_key"] = str(persona["persona_key"])
            persona["display_name"] = str(persona["display_name"])
            persona["enabled"] = bool(persona["enabled"])
            persona["created_at"] = _iso(persona.get("created_at"))
            persona["updated_at"] = _iso(persona.get("updated_at"))
            cursor.execute(
                sql.SQL(
                    "SELECT version,status,content,change_note,based_on_version,created_by,created_at,"
                    "published_by,published_at FROM {} WHERE persona_key=%s ORDER BY version"
                ).format(self._table("support_account_prompt_versions")),
                (persona["persona_key"],),
            )
            persona["versions"] = [
                {
                    "persona_key": persona["persona_key"],
                    "version": int(row["version"]),
                    "status": str(row["status"]),
                    "content": _json_dict(row.get("content")),
                    "change_note": str(row.get("change_note") or ""),
                    "based_on_version": row.get("based_on_version"),
                    "created_by": str(row.get("created_by") or ""),
                    "created_at": _iso(row.get("created_at")),
                    "published_by": str(row.get("published_by") or "").strip() or None,
                    "published_at": _iso(row.get("published_at")),
                }
                for row in cursor.fetchall()
            ]
        return personas

    def _managed_prompts(self, cursor: psycopg.Cursor[dict[str, Any]]) -> list[dict[str, Any]]:
        cursor.execute(
            sql.SQL("SELECT release_id FROM {} WHERE status='active' LIMIT 1").format(
                self._table("support_prompt_releases")
            )
        )
        active_row = cursor.fetchone()
        release_id = str(active_row["release_id"]) if active_row else None
        cursor.execute(
            sql.SQL(
                "SELECT prompt_key,name,agent_key,component_key,editable,created_at,updated_at "
                "FROM {} WHERE retired_at IS NULL ORDER BY agent_key,component_key,prompt_key"
            ).format(self._table("support_prompt_definitions"))
        )
        prompts = []
        for definition in cursor.fetchall():
            key = str(definition["prompt_key"])
            cursor.execute(
                sql.SQL(
                    "SELECT version,content,content_sha256,status,based_on_version,change_note,"
                    "created_by,created_at,scheduled_by,scheduled_at,activated_at FROM {} "
                    "WHERE prompt_key=%s ORDER BY version DESC"
                ).format(self._table("support_prompt_versions")),
                (key,),
            )
            versions = [
                {
                    "prompt_key": key,
                    "version": int(row["version"]),
                    "content": str(row.get("content") or ""),
                    "content_sha256": str(row.get("content_sha256") or ""),
                    "status": str(row.get("status") or ""),
                    "based_on_version": row.get("based_on_version"),
                    "change_note": str(row.get("change_note") or ""),
                    "created_by": str(row.get("created_by") or ""),
                    "created_at": _iso(row.get("created_at")),
                    "scheduled_by": str(row.get("scheduled_by") or "").strip() or None,
                    "scheduled_at": _iso(row.get("scheduled_at")),
                    "activated_at": _iso(row.get("activated_at")),
                }
                for row in cursor.fetchall()
            ]
            prompts.append(
                {
                    "prompt_key": key,
                    "name": str(definition.get("name") or key),
                    "agent_key": str(definition.get("agent_key") or ""),
                    "component_key": str(definition.get("component_key") or ""),
                    "editable": bool(definition.get("editable", True)),
                    "created_at": _iso(definition.get("created_at")),
                    "updated_at": _iso(definition.get("updated_at")),
                    "versions": versions,
                    "active_version": next((item for item in versions if item["status"] == "active"), None),
                    "scheduled_version": next((item for item in versions if item["status"] == "scheduled"), None),
                    "active_release_id": release_id,
                }
            )
        return prompts

    def agent_config(self) -> dict[str, Any]:
        with self._read_cursor() as cursor:
            return build_agent_config_payload(
                self._personas(cursor),
                self._managed_prompts(cursor),
            )

    def environment_config(self) -> dict[str, Any]:
        names = sorted(name for name in os.environ if _ENV_KEY_RE.fullmatch(name))
        return {
            "names": names,
            "items": [
                {"name": name, "description": environment_config_description(name)}
                for name in names
            ],
        }


class _AccountRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def list_account_cases(self, **_: Any) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]


def _automation_usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stage_totals: dict[str, dict[str, int]] = {}
    model_totals: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        prompt_tokens = max(0, int(row.get("prompt_tokens") or 0))
        completion_tokens = max(0, int(row.get("completion_tokens") or 0))
        cached_tokens = max(0, int(row.get("cached_input_tokens") or 0))
        reasoning_tokens = max(0, int(row.get("reasoning_tokens") or 0))
        stage = str(row.get("stage") or "unknown")
        stage_bucket = stage_totals.setdefault(
            stage,
            {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
        )
        stage_bucket["calls"] += 1
        stage_bucket["input_tokens"] += prompt_tokens
        stage_bucket["cached_input_tokens"] += cached_tokens
        stage_bucket["output_tokens"] += completion_tokens
        stage_bucket["reasoning_tokens"] += reasoning_tokens
        provider = str(row.get("provider") or "")
        model = str(row.get("model") or "")
        model_bucket = model_totals.setdefault(
            (provider, model),
            {
                "provider": provider,
                "model": model,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "embedding_tokens": 0,
            },
        )
        model_bucket["input_tokens"] += prompt_tokens
        model_bucket["cached_input_tokens"] += cached_tokens
        model_bucket["output_tokens"] += completion_tokens
    return {
        "available": True,
        "call_count": len(rows),
        "total_input_tokens": sum(item["input_tokens"] for item in stage_totals.values()),
        "total_cached_input_tokens": sum(item["cached_input_tokens"] for item in stage_totals.values()),
        "total_output_tokens": sum(item["output_tokens"] for item in stage_totals.values()),
        "stage_totals": stage_totals,
        "token_by_model": list(model_totals.values()),
    }


def create_automation_ecs_admin_reader(
    settings: AutomationEcsSettings,
) -> AutomationEcsAdminReader | EmptyAutomationEcsAdminReader:
    if settings.allow_memory:
        return EmptyAutomationEcsAdminReader()
    return AutomationEcsAdminReader(settings)

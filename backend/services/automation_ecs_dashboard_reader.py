"""Read-only, Ticket-centric data contract for the ECS Production dashboard."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from backend.services.account_case_filters import (
    account_case_filter_definitions,
    account_case_filter_key,
    account_case_filter_keys,
    account_case_filter_matches,
    account_case_filter_memberships,
    normalize_account_case_filter,
)
from backend.services.automation_ecs_runtime import AutomationEcsSettings


TICKET_STATUSES = ("new", "open", "pending", "hold", "solved", "closed", "unknown")
ACTIVE_TICKET_STATUSES = frozenset({"new", "open", "pending", "hold", "unknown"})
_GROUP_LABELS = {
    definition["id"]: definition["label"]
    for definition in account_case_filter_definitions()
}
_GROUP_LABELS["agora_non_technical"] = "Agora Non-Technical"
_COLLECTED_FIELD_ALLOWLISTS = {
    "enablement": ("app_id", "requested_feature", "requested_feature_label"),
    "fraud_account": (
        "account_type",
        "name",
        "office_address",
        "contact_number",
        "contact_email",
        "use_case_description",
        "console_configuration",
    ),
    "detailed_invoice": ("issue_date", "transaction_id", "amount"),
    "quota": (
        "request_type",
        "products",
        "app_ids",
        "requested_limits",
        "event_name",
        "event_start",
        "event_timezone",
        "event_duration",
        "expected_peak_concurrency",
        "original_request_labels",
    ),
    "account_suspension": (
        "suspension_status_or_error",
        "known_reason",
        "customer_actions_taken",
    ),
}


class DashboardCaseReader(Protocol):
    def list_cases(
        self,
        *,
        page: int,
        page_size: int,
        zendesk_ticket_id: str | None = None,
        execution_id: str | None = None,
        route_group: str | None = None,
        route_subcategory: str | None = None,
        ticket_status: str = "active",
        execution_status: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]: ...

    def get_case(self, zendesk_ticket_id: str) -> dict[str, Any] | None: ...


class EmptyDashboardCaseReader:
    """Keep memory-only local API fixtures usable without inventing Case data."""

    def list_cases(self, *, page: int, page_size: int, **_: Any) -> dict[str, Any]:
        return {
            "items": [],
            "page": page,
            "page_size": page_size,
            "total": 0,
            "pages": 0,
            "facets": {
                "route_groups": {key: 0 for key in account_case_filter_keys()},
                "route_subcategories": {},
                "ticket_statuses": {"active": 0, "all": 0, **{key: 0 for key in TICKET_STATUSES}},
            },
            "filter_definitions": account_case_filter_definitions(),
        }

    def get_case(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        del zendesk_ticket_id
        return None


def _ticket_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in TICKET_STATUSES[:-1] else "unknown"


def _timestamp_sort_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


_SENSITIVE_NESTED_KEYS = frozenset(
    {
        "authorization",
        "claim_token",
        "credential",
        "credentials",
        "dsn",
        "internal_email_body",
        "internal_email_payload",
        "password",
        "passcode",
        "payload",
        "private_key",
        "prompt",
        "prompt_snapshot",
        "secret",
        "session_secret",
        "token",
    }
)


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:20_000]
    return None


def _safe_nested_key(value: Any) -> str | None:
    key = str(value or "").strip()[:160]
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    parts = {part for part in normalized.split("_") if part}
    if (
        not key
        or normalized in _SENSITIVE_NESTED_KEYS
        or parts.intersection({"password", "passcode", "secret", "token", "credential", "dsn"})
    ):
        return None
    return key


def _safe_collected_value(value: Any) -> Any:
    scalar = _safe_scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, list):
        items = [_safe_scalar(item) for item in value[:100]]
        return [item for item in items if item is not None]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_item in list(value.items())[:100]:
            key = _safe_nested_key(raw_key)
            if key is None:
                continue
            item = _safe_scalar(raw_item)
            if item is None and isinstance(raw_item, list):
                values = [_safe_scalar(entry) for entry in raw_item[:100]]
                item = [entry for entry in values if entry is not None]
            if item not in (None, "", [], {}):
                result[key] = item
        return result
    return None


def _safe_collected_fields(value: Any, *, handler: Any, subcategory: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized_subcategory = str(subcategory or "").strip().lower()
    normalized_handler = str(handler or "").strip().lower()
    contract = normalized_subcategory if normalized_subcategory in _COLLECTED_FIELD_ALLOWLISTS else normalized_handler
    allowed = _COLLECTED_FIELD_ALLOWLISTS.get(contract, ())
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in value:
            continue
        safe_value = _safe_collected_value(value[key])
        if safe_value not in (None, "", [], {}):
            result[key] = safe_value
    return result


def _source_candidate(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("Link") or value.get("link") or value.get("url") or "").strip()
    raw = str(value or "").strip()
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return ""
        return _source_candidate(parsed)
    return raw


def safe_zendesk_source(value: Any, zendesk_ticket_id: str) -> str | None:
    ticket_id = str(zendesk_ticket_id or "").strip()
    candidate = _source_candidate(value)
    if not candidate and ticket_id.isdigit():
        candidate = f"https://agoraio.zendesk.com/agent/tickets/{ticket_id}"
    try:
        parsed = urlsplit(candidate)
        host = str(parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    allowed_host = host == "zendesk.com" or host.endswith(".zendesk.com")
    if (
        parsed.scheme != "https"
        or not allowed_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        return None
    expected_suffix = f"/agent/tickets/{ticket_id}"
    if not ticket_id.isdigit() or parsed.path.rstrip("/") != expected_suffix:
        return None
    return candidate


def _route_view(row: dict[str, Any]) -> dict[str, Any]:
    primary = account_case_filter_key(row)
    group, _, leaf = primary.partition(":")
    definitions = account_case_filter_definitions()
    child_labels = {
        (definition["id"], child["id"]): child["label"]
        for definition in definitions
        for child in definition["children"]
    }
    subcategory = leaf or str(row.get("subcategory") or "").strip().lower() or None
    return {
        "product": "Agora",
        "category": group,
        "category_label": _GROUP_LABELS.get(group, group.replace("_", " ").title()),
        "subcategory": subcategory,
        "subcategory_label": (
            child_labels.get((group, subcategory), subcategory.replace("_", " ").title())
            if subcategory
            else None
        ),
    }


def _execution_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "execution_id",
            "event_id",
            "event_type",
            "status",
            "current_stage",
            "failure_stage",
            "failure_code",
            "requires_human_review",
            "created_at",
            "updated_at",
        )
    }


class PostgresDashboardCaseReader:
    def __init__(self, settings: AutomationEcsSettings) -> None:
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

    def _list_rows(
        self,
        cursor: psycopg.Cursor[dict[str, Any]],
        *,
        zendesk_ticket_id: str | None,
        execution_id: str | None,
        execution_status: str | None,
        event_type: str | None,
    ) -> list[dict[str, Any]]:
        execution_filters = [sql.SQL("execution.namespace=%s")]
        execution_params: list[Any] = [self.settings.job_namespace]
        for column, value in (
            ("execution_id", execution_id),
            ("status", execution_status),
            ("event_type", event_type),
        ):
            if value is not None:
                execution_filters.append(
                    sql.SQL("execution.{}=%s").format(sql.Identifier(column))
                )
                execution_params.append(value)
        case_filters = [sql.SQL("automation_case.namespace=%s")]
        case_params: list[Any] = [self.settings.job_namespace]
        if zendesk_ticket_id is not None:
            case_filters.append(sql.SQL("automation_case.zendesk_ticket_id=%s"))
            case_params.append(zendesk_ticket_id)
        cursor.execute(
            sql.SQL(
                """
                SELECT
                    automation_case.zendesk_ticket_id,
                    COALESCE(NULLIF(automation_case.ticket->>'subject', ''), account_case.title, '') AS title,
                    COALESCE(NULLIF(LOWER(automation_case.ticket->>'status'), ''),
                        LOWER(account_case.zendesk_ticket_status), 'unknown') AS ticket_status,
                    COALESCE(NULLIF(automation_case.ticket->>'updated_at', '')::TIMESTAMPTZ,
                        account_case.updated_at, automation_case.updated_at) AS ticket_updated_at,
                    account_case.automation_status,
                    account_case.route,
                    account_case.scope_label,
                    account_case.route_family,
                    account_case.execution_action,
                    COALESCE(account_case.category, execution.route->>'category') AS category,
                    COALESCE(account_case.subcategory, execution.route->>'subcategory') AS subcategory,
                    account_case.route_status,
                    account_case.automation_handler,
                    account_case.route_classification,
                    execution.execution_id,
                    execution.event_id,
                    execution.event_type,
                    execution.status,
                    execution.current_stage,
                    execution.failure_stage,
                    execution.failure_code,
                    execution.requires_human_review,
                    execution.created_at,
                    execution.updated_at,
                    history.execution_count
                FROM {automation_cases} AS automation_case
                JOIN LATERAL (
                    SELECT execution_id,event_id,event_type,status,current_stage,failure_stage,
                        failure_code,requires_human_review,route,created_at,updated_at
                    FROM {executions} AS execution
                    WHERE execution.zendesk_ticket_id=automation_case.zendesk_ticket_id
                      AND {execution_where}
                    ORDER BY execution.updated_at DESC, execution.created_at DESC
                    LIMIT 1
                ) AS execution ON TRUE
                LEFT JOIN {account_cases} AS account_case
                  ON account_case.processing_profile='production'
                 AND account_case.zendesk_ticket_id=automation_case.zendesk_ticket_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(*)::INTEGER AS execution_count
                    FROM {executions} AS all_execution
                    WHERE all_execution.namespace=%s
                      AND all_execution.zendesk_ticket_id=automation_case.zendesk_ticket_id
                ) AS history ON TRUE
                WHERE {case_where}
                """
            ).format(
                automation_cases=self._table("automation_cases"),
                executions=self._table("automation_executions"),
                account_cases=self._table("support_account_cases"),
                execution_where=sql.SQL(" AND ").join(execution_filters),
                case_where=sql.SQL(" AND ").join(case_filters),
            ),
            (*execution_params, self.settings.job_namespace, *case_params),
        )
        return list(cursor.fetchall())

    def list_cases(
        self,
        *,
        page: int,
        page_size: int,
        zendesk_ticket_id: str | None = None,
        execution_id: str | None = None,
        route_group: str | None = None,
        route_subcategory: str | None = None,
        ticket_status: str = "active",
        execution_status: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        route_filter = normalize_account_case_filter(
            group=route_group,
            subcategory=route_subcategory,
        )
        with self._read_cursor() as cursor:
            rows = self._list_rows(
                cursor,
                zendesk_ticket_id=zendesk_ticket_id,
                execution_id=execution_id,
                execution_status=execution_status,
                event_type=event_type,
            )

        for row in rows:
            row["ticket_status"] = _ticket_status(row.get("ticket_status"))

        status_filtered = [
            row
            for row in rows
            if ticket_status == "all"
            or (ticket_status == "active" and row["ticket_status"] in ACTIVE_TICKET_STATUSES)
            or row["ticket_status"] == ticket_status
        ]
        filtered = [row for row in status_filtered if account_case_filter_matches(row, route_filter)]
        filtered.sort(
            key=lambda row: (
                _timestamp_sort_value(row.get("ticket_updated_at")),
                str(row.get("zendesk_ticket_id") or ""),
            ),
            reverse=True,
        )

        route_counts = {key: 0 for key in account_case_filter_keys()}
        for row in status_filtered:
            route_counts["all"] += 1
            for membership in account_case_filter_memberships(row):
                if membership in route_counts:
                    route_counts[membership] += 1

        route_only_rows = [row for row in rows if account_case_filter_matches(row, route_filter)]
        status_counts = {"all": len(route_only_rows), "active": 0, **{key: 0 for key in TICKET_STATUSES}}
        for row in route_only_rows:
            status_counts[row["ticket_status"]] += 1
            if row["ticket_status"] in ACTIVE_TICKET_STATUSES:
                status_counts["active"] += 1

        selected_group = str(route_group or "all").strip().lower()
        subcategory_counts: dict[str, int] = {}
        if selected_group not in {"", "all"}:
            group_rows = [
                row
                for row in status_filtered
                if account_case_filter_matches(row, selected_group)
            ]
            for row in group_rows:
                prefix = f"{selected_group}:"
                for membership in account_case_filter_memberships(row):
                    if membership.startswith(prefix):
                        child = membership[len(prefix) :]
                        subcategory_counts[child] = subcategory_counts.get(child, 0) + 1

        total = len(filtered)
        offset = (page - 1) * page_size
        items = []
        for row in filtered[offset : offset + page_size]:
            route = _route_view(row)
            items.append(
                {
                    "zendesk_ticket_id": str(row["zendesk_ticket_id"]),
                    "title": str(row.get("title") or ""),
                    "ticket_status": row["ticket_status"],
                    "updated_at": row.get("ticket_updated_at"),
                    "automation_status": row.get("automation_status") or row.get("status"),
                    "route": route,
                    "matched_execution_id": row.get("execution_id"),
                    "current_execution": _execution_summary(row),
                    "execution_count": int(row.get("execution_count") or 0),
                }
            )
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size,
            "facets": {
                "route_groups": route_counts,
                "route_subcategories": subcategory_counts,
                "ticket_statuses": status_counts,
            },
            "filter_definitions": account_case_filter_definitions(),
        }

    def _base_case(
        self,
        cursor: psycopg.Cursor[dict[str, Any]],
        zendesk_ticket_id: str,
    ) -> dict[str, Any] | None:
        cursor.execute(
            sql.SQL(
                """
                SELECT automation_case.zendesk_ticket_id,
                    COALESCE(NULLIF(automation_case.ticket->>'subject', ''), account_case.title, '') AS title,
                    automation_case.ticket->>'description' AS ticket_description,
                    COALESCE(NULLIF(LOWER(automation_case.ticket->>'status'), ''),
                        LOWER(account_case.zendesk_ticket_status), 'unknown') AS ticket_status,
                    COALESCE(NULLIF(automation_case.ticket->>'updated_at', '')::TIMESTAMPTZ,
                        account_case.updated_at, automation_case.updated_at) AS ticket_updated_at,
                    automation_case.current_execution_id,
                    account_case.account_case_id,
                    account_case.client_ticket_id,
                    account_case.source,
                    account_case.automation_status,
                    account_case.route,
                    account_case.scope_label,
                    account_case.route_family,
                    account_case.execution_action,
                    account_case.category,
                    account_case.subcategory,
                    account_case.route_status,
                    account_case.automation_handler,
                    account_case.route_classification,
                    account_case.collected_fields,
                    account_case.zendesk_status_updated_at,
                    account_case.zendesk_status_synced_at,
                    comment_sync.synced_at AS comment_synced_at,
                    persona_assignment.persona_key,
                    persona_assignment.version AS persona_version,
                    persona.display_name AS persona_display_name
                FROM {automation_cases} AS automation_case
                LEFT JOIN {account_cases} AS account_case
                  ON account_case.processing_profile='production'
                 AND account_case.zendesk_ticket_id=automation_case.zendesk_ticket_id
                LEFT JOIN {comment_sync} AS comment_sync
                  ON comment_sync.client_ticket_id=account_case.client_ticket_id
                LEFT JOIN {persona_assignments} AS persona_assignment
                  ON persona_assignment.ticket_id=account_case.client_ticket_id
                LEFT JOIN {personas} AS persona
                  ON persona.persona_key=persona_assignment.persona_key
                WHERE automation_case.namespace=%s
                  AND automation_case.zendesk_ticket_id=%s
                LIMIT 1
                """
            ).format(
                automation_cases=self._table("automation_cases"),
                account_cases=self._table("support_account_cases"),
                comment_sync=self._table("support_account_case_comment_sync_state"),
                persona_assignments=self._table("support_account_persona_assignments"),
                personas=self._table("support_account_personas"),
            ),
            (self.settings.job_namespace, zendesk_ticket_id),
        )
        return cursor.fetchone()

    def _execution_history(
        self,
        cursor: psycopg.Cursor[dict[str, Any]],
        zendesk_ticket_id: str,
    ) -> list[dict[str, Any]]:
        cursor.execute(
            sql.SQL(
                """
                SELECT execution_id,event_id,event_type,status,current_stage,failure_stage,
                    failure_code,requires_human_review,created_at,updated_at
                FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s
                ORDER BY created_at DESC
                """
            ).format(self._table("automation_executions")),
            (self.settings.job_namespace, zendesk_ticket_id),
        )
        return [_execution_summary(row) for row in cursor.fetchall()]

    def _conversation(
        self,
        cursor: psycopg.Cursor[dict[str, Any]],
        case: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ticket_id = str(case["zendesk_ticket_id"])
        messages: dict[str, dict[str, Any]] = {}
        cursor.execute(
            sql.SQL(
                "SELECT zendesk_comment_id,comment FROM {} "
                "WHERE namespace=%s AND zendesk_ticket_id=%s"
            ).format(self._table("automation_case_comments")),
            (self.settings.job_namespace, ticket_id),
        )
        for row in cursor.fetchall():
            comment = row.get("comment") if isinstance(row.get("comment"), dict) else {}
            author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
            comment_id = str(row.get("zendesk_comment_id") or comment.get("id") or "").strip()
            if not comment_id:
                continue
            author_kind = str(author.get("role") or "customer").strip().lower()
            if author.get("is_agent") is True:
                author_kind = "agent"
            messages[f"zendesk:{comment_id}"] = {
                "id": f"zendesk:{comment_id}",
                "source": "zendesk",
                "visibility": "public" if bool(comment.get("public")) else "internal",
                "author_kind": author_kind,
                "body": str(comment.get("body") or ""),
                "created_at": comment.get("created_at"),
            }

        client_ticket_id = str(case.get("client_ticket_id") or "").strip()
        account_case_id = str(case.get("account_case_id") or "").strip()
        if client_ticket_id:
            cursor.execute(
                sql.SQL(
                    """
                    SELECT zendesk_comment_id,is_public,author_kind,body,created_at
                    FROM {} WHERE client_ticket_id=%s
                    ORDER BY created_at,zendesk_comment_id
                    """
                ).format(self._table("support_account_case_comments")),
                (client_ticket_id,),
            )
            for row in cursor.fetchall():
                comment_id = str(row.get("zendesk_comment_id") or "").strip()
                messages[f"zendesk:{comment_id}"] = {
                    "id": f"zendesk:{comment_id}",
                    "source": "zendesk",
                    "visibility": "public" if bool(row.get("is_public")) else "internal",
                    "author_kind": str(row.get("author_kind") or "unknown"),
                    "body": str(row.get("body") or ""),
                    "created_at": row.get("created_at"),
                }
            cursor.execute(
                sql.SQL(
                    """
                    SELECT message.id,message.content,message.created_at,delivery.status AS delivery_status,
                        delivery.is_public,delivery.zendesk_comment_id
                    FROM {messages} AS message
                    LEFT JOIN {deliveries} AS delivery
                      ON delivery.account_case_id=%s AND delivery.message_id=message.id::TEXT
                    WHERE message.ticket_id=%s AND message.role='assistant'
                      AND COALESCE(message.meta->>'source','')='account_ai'
                      AND COALESCE(message.meta->>'superseded','false')<>'true'
                    ORDER BY message.created_at,message.id
                    """
                ).format(
                    messages=self._table("support_ticket_messages"),
                    deliveries=self._table("support_account_zendesk_comment_deliveries"),
                ),
                (account_case_id, client_ticket_id),
            )
            for row in cursor.fetchall():
                linked_comment_id = str(row.get("zendesk_comment_id") or "").strip()
                if linked_comment_id and f"zendesk:{linked_comment_id}" in messages:
                    continue
                if str(row.get("delivery_status") or "").strip().lower() == "delivered":
                    continue
                message_id = str(row.get("id") or "").strip()
                messages[f"local:{message_id}"] = {
                    "id": f"local:{message_id}",
                    "source": "local",
                    "visibility": "public" if bool(row.get("is_public")) else "internal",
                    "author_kind": "automation",
                    "body": str(row.get("content") or ""),
                    "delivery_status": str(row.get("delivery_status") or "local_only"),
                    "created_at": row.get("created_at"),
                }

        if not any(key.startswith("zendesk:") for key in messages):
            description = str(case.get("ticket_description") or "").strip()
            if description:
                messages[f"ticket:{ticket_id}:description"] = {
                    "id": f"ticket:{ticket_id}:description",
                    "source": "zendesk",
                    "visibility": "public",
                    "author_kind": "customer",
                    "body": description,
                    "created_at": case.get("ticket_updated_at"),
                }
        return sorted(
            messages.values(),
            key=lambda item: (_timestamp_sort_value(item.get("created_at")), item["id"]),
        )

    def _pending_reply(
        self,
        cursor: psycopg.Cursor[dict[str, Any]],
        client_ticket_id: str,
    ) -> dict[str, Any] | None:
        if not client_ticket_id:
            return None
        cursor.execute(
            sql.SQL(
                """
                SELECT job_id,status,scheduled_for,payload,attempt_count,created_at,updated_at
                FROM {} WHERE ticket_id=%s AND published_at IS NULL
                  AND status NOT IN ('published','cancelled')
                ORDER BY created_at DESC LIMIT 1
                """
            ).format(self._table("support_account_reply_jobs")),
            (client_ticket_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        preview = str(payload.get("generated_content") or payload.get("draft_content") or "").strip()
        status = str(row.get("status") or "unknown")
        return {
            "job_id": row.get("job_id"),
            "status": status,
            "scheduled_for": row.get("scheduled_for"),
            "attempt": int(row.get("attempt_count") or 0),
            "preview": preview or None,
            "preview_state": "ready" if preview else "preparing" if "prepar" in status or "queued" in status else "unavailable",
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def get_case(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._read_cursor() as cursor:
            case = self._base_case(cursor, zendesk_ticket_id)
            if case is None:
                return None
            executions = self._execution_history(cursor, zendesk_ticket_id)
            conversation = self._conversation(cursor, case)
            pending_reply = self._pending_reply(
                cursor,
                str(case.get("client_ticket_id") or "").strip(),
            )
        route = _route_view(case)
        persona = None
        if case.get("persona_key"):
            persona = {
                "persona_key": case.get("persona_key"),
                "display_name": case.get("persona_display_name") or case.get("persona_key"),
                "version": case.get("persona_version"),
            }
        return {
            "zendesk_ticket_id": str(case["zendesk_ticket_id"]),
            "title": str(case.get("title") or ""),
            "source_url": safe_zendesk_source(case.get("source"), zendesk_ticket_id),
            "automation_status": case.get("automation_status") or (executions[0].get("status") if executions else None),
            "ticket_status": _ticket_status(case.get("ticket_status")),
            "zendesk_status_updated_at": case.get("zendesk_status_updated_at"),
            "zendesk_status_synced_at": case.get("zendesk_status_synced_at") or case.get("comment_synced_at"),
            "updated_at": case.get("ticket_updated_at"),
            "persona": persona,
            "route": route,
            "collected_fields": _safe_collected_fields(
                case.get("collected_fields"),
                handler=case.get("automation_handler"),
                subcategory=route.get("subcategory"),
            ),
            "conversation": conversation,
            "pending_reply": pending_reply,
            "current_execution_id": case.get("current_execution_id"),
            "executions": executions,
        }


def create_dashboard_case_reader(settings: AutomationEcsSettings) -> DashboardCaseReader:
    if settings.allow_memory:
        return EmptyDashboardCaseReader()
    return PostgresDashboardCaseReader(settings)

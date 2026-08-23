"""Small environment-local execution ledger used by the new runtimes."""

from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


ROUTE_CATEGORY_SQL = "COALESCE(NULLIF(payload->'route_result'->'route'->>'category',''),'uncategorized')"
ROUTE_SUBCATEGORY_SQL = "payload->'route_result'->'route'->>'subcategory'"


def _route_field_of(record: dict[str, Any], field: str, default: str) -> str:
    route_result = record.get("route_result") if isinstance(record.get("route_result"), dict) else {}
    route = route_result.get("route") if isinstance(route_result.get("route"), dict) else {}
    return str(route.get(field) or "").strip() or default


def _route_category_of(record: dict[str, Any]) -> str:
    return _route_field_of(record, "category", "uncategorized")


def _route_subcategory_of(record: dict[str, Any]) -> str:
    return _route_field_of(record, "subcategory", "")


class AutomationExecutionStore:
    def __init__(self, *, environment: str) -> None:
        self.environment = environment
        self._lock = threading.RLock()
        self._memory: dict[str, dict[str, Any]] = {}
        self._dsn = str(os.getenv("AUTOMATION_DB_DSN") or "").strip()
        self._schema = str(os.getenv("AUTOMATION_DB_SCHEMA") or "supportportal").strip()
        self._table_name = str(os.getenv("AUTOMATION_DB_TABLE") or f"automation_executions_{environment}").strip()
        if not self._schema.replace("_", "").isalnum():
            raise RuntimeError("AUTOMATION_DB_SCHEMA must be alphanumeric")
        if not self._table_name.replace("_", "").isalnum():
            raise RuntimeError("AUTOMATION_DB_TABLE must be alphanumeric")

    @property
    def in_memory(self) -> bool:
        return not self._dsn

    def _table(self) -> str:
        return f'"{self._schema}"."{self._table_name}"'

    def ensure_schema(self) -> None:
        if self.in_memory:
            if os.getenv("AUTOMATION_RUNTIME_ALLOW_MEMORY") != "1":
                raise RuntimeError("AUTOMATION_DB_DSN is required")
            return
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table()} (
                        execution_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL UNIQUE,
                        environment TEXT NOT NULL,
                        case_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )

    def save(self, record: dict[str, Any]) -> dict[str, Any]:
        saved = copy.deepcopy(record)
        saved.setdefault("execution_id", f"exec-{uuid4().hex}")
        saved.setdefault("created_at", _now())
        saved["updated_at"] = _now()
        saved["environment"] = self.environment
        if self.in_memory:
            with self._lock:
                existing = next(
                    (item for item in self._memory.values() if item.get("request_id") == saved.get("request_id")),
                    None,
                )
                if existing is not None:
                    saved["execution_id"] = existing["execution_id"]
                    saved["created_at"] = existing.get("created_at") or saved["created_at"]
                    existing.update(saved)
                    return copy.deepcopy(existing)
                self._memory[str(saved["execution_id"])] = saved
                return copy.deepcopy(saved)
        self.ensure_schema()
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._table()}
                        (execution_id, request_id, environment, case_id, status, payload, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (request_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        payload = EXCLUDED.payload,
                        updated_at = EXCLUDED.updated_at
                    RETURNING execution_id, request_id, environment, case_id, status, payload,
                              created_at::text, updated_at::text
                    """,
                    (
                        saved["execution_id"],
                        saved["request_id"],
                        saved["environment"],
                        saved["case_id"],
                        saved["status"],
                        json.dumps(saved, ensure_ascii=False),
                        saved["created_at"],
                        saved["updated_at"],
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("execution ledger insert returned no row")
        payload = row[5] if isinstance(row[5], dict) else json.loads(row[5])
        payload.update(
            execution_id=row[0],
            request_id=row[1],
            environment=row[2],
            case_id=row[3],
            status=row[4],
            created_at=row[6],
            updated_at=row[7],
        )
        return payload

    def get_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized:
            return None
        if self.in_memory:
            with self._lock:
                value = next(
                    (item for item in self._memory.values() if item.get("request_id") == normalized),
                    None,
                )
                return copy.deepcopy(value) if value else None
        self.ensure_schema()
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT payload FROM {self._table()} WHERE request_id=%s",
                    (normalized,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return copy.deepcopy(row[0] if isinstance(row[0], dict) else json.loads(row[0]))

    def get(self, execution_id: str) -> dict[str, Any] | None:
        if self.in_memory:
            with self._lock:
                value = self._memory.get(str(execution_id))
                return copy.deepcopy(value) if value else None
        self.ensure_schema()
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT payload FROM {self._table()} WHERE execution_id=%s",
                    (str(execution_id),),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return copy.deepcopy(row[0] if isinstance(row[0], dict) else json.loads(row[0]))

    def list_executions(
        self,
        *,
        status: str | None = None,
        case_id: str | None = None,
        route_category: str | None = None,
        route_subcategory: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return one page of executions plus totals and filter-facet counts.

        ``status_counts``/``route_counts`` follow the search context (``case_id``)
        but ignore the matching filter itself so every filter control shows its
        own count from the same snapshot. ``route_subcategory_counts`` is only
        populated when a ``route_category`` filter is selected.
        """
        normalized_status = str(status or "").strip() or None
        normalized_case_id = str(case_id or "").strip() or None
        normalized_route_category = str(route_category or "").strip() or None
        normalized_route_subcategory = str(route_subcategory or "").strip() or None
        if self.in_memory:
            with self._lock:
                records = [copy.deepcopy(item) for item in self._memory.values()]
            if normalized_case_id is not None:
                records = [item for item in records if item.get("case_id") == normalized_case_id]
            context_records = records
            status_counts: dict[str, int] = {}
            for item in context_records:
                key = str(item.get("status") or "")
                status_counts[key] = status_counts.get(key, 0) + 1
            route_counts: dict[str, int] = {}
            for item in context_records:
                key = _route_category_of(item)
                route_counts[key] = route_counts.get(key, 0) + 1
            route_subcategory_counts: dict[str, int] = {}
            if normalized_route_category is not None:
                for item in context_records:
                    if _route_category_of(item) != normalized_route_category:
                        continue
                    key = _route_subcategory_of(item)
                    route_subcategory_counts[key] = route_subcategory_counts.get(key, 0) + 1
            if normalized_status is not None:
                records = [item for item in records if str(item.get("status") or "") == normalized_status]
            if normalized_route_category is not None:
                records = [item for item in records if _route_category_of(item) == normalized_route_category]
            if normalized_route_subcategory is not None:
                records = [item for item in records if _route_subcategory_of(item) == normalized_route_subcategory]
            records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            total = len(records)
            items = records[offset : offset + limit]
            return {
                "items": items,
                "total": total,
                "status_counts": status_counts,
                "route_counts": route_counts,
                "route_subcategory_counts": route_subcategory_counts,
            }
        self.ensure_schema()
        page_filters: list[str] = []
        count_filters: list[str] = []
        parameters: list[Any] = []
        count_parameters: list[Any] = []
        if normalized_status is not None:
            page_filters.append("status=%s")
            parameters.append(normalized_status)
        if normalized_case_id is not None:
            page_filters.append("case_id=%s")
            parameters.append(normalized_case_id)
            count_filters.append("case_id=%s")
            count_parameters.append(normalized_case_id)
        if normalized_route_category is not None:
            page_filters.append(f"{ROUTE_CATEGORY_SQL}=%s")
            parameters.append(normalized_route_category)
        if normalized_route_subcategory is not None:
            page_filters.append(f"{ROUTE_SUBCATEGORY_SQL}=%s")
            parameters.append(normalized_route_subcategory)
        page_where = f" WHERE {' AND '.join(page_filters)}" if page_filters else ""
        count_where = f" WHERE {' AND '.join(count_filters)}" if count_filters else ""
        subcategory_where_parts = list(count_filters) + [f"{ROUTE_CATEGORY_SQL}=%s"]
        subcategory_where = " WHERE " + " AND ".join(subcategory_where_parts)
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {self._table()}{page_where}",
                    parameters,
                )
                total = int(cursor.fetchone()[0])
                cursor.execute(
                    f"SELECT status, COUNT(*) FROM {self._table()}{count_where} GROUP BY status",
                    count_parameters,
                )
                status_counts = {str(row[0]): int(row[1]) for row in cursor.fetchall()}
                cursor.execute(
                    f"SELECT {ROUTE_CATEGORY_SQL} AS category, COUNT(*) FROM {self._table()}{count_where} GROUP BY 1 ORDER BY 2 DESC, 1",
                    count_parameters,
                )
                route_counts = {str(row[0]): int(row[1]) for row in cursor.fetchall()}
                route_subcategory_counts = {}
                if normalized_route_category is not None:
                    cursor.execute(
                        f"SELECT {ROUTE_SUBCATEGORY_SQL} AS subcategory, COUNT(*) FROM {self._table()}{subcategory_where} GROUP BY 1 ORDER BY 2 DESC, 1",
                        [*count_parameters, normalized_route_category],
                    )
                    route_subcategory_counts = {str(row[0]): int(row[1]) for row in cursor.fetchall()}
                cursor.execute(
                    f"SELECT payload FROM {self._table()}{page_where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    [*parameters, limit, offset],
                )
                items = [
                    copy.deepcopy(row[0] if isinstance(row[0], dict) else json.loads(row[0]))
                    for row in cursor.fetchall()
                ]
        return {
            "items": items,
            "total": total,
            "status_counts": status_counts,
            "route_counts": route_counts,
            "route_subcategory_counts": route_subcategory_counts,
        }

    def delete_all(self) -> int:
        """Delete every execution row in this environment's table."""
        if self.in_memory:
            with self._lock:
                deleted = len(self._memory)
                self._memory.clear()
            return deleted
        self.ensure_schema()
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._table()}")
                deleted = int(cursor.rowcount)
            connection.commit()
        return deleted

from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.types.json import Json
try:
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover - exercised in lightweight dependency checks
    ConnectionPool = None


ASSET_STATUS_PENDING_UPLOAD = "pending_upload"
ASSET_STATUS_UPLOADED = "uploaded"
ASSET_STATUS_ATTACHED = "attached"
VALID_ASSET_STATUSES = {
    ASSET_STATUS_PENDING_UPLOAD,
    ASSET_STATUS_UPLOADED,
    ASSET_STATUS_ATTACHED,
    "rejected",
    "deleted",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_status(value: Any) -> str:
    status = str(value or ASSET_STATUS_PENDING_UPLOAD).strip().lower()
    return status if status in VALID_ASSET_STATUSES else ASSET_STATUS_PENDING_UPLOAD


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _asset_row_to_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "asset_id": str(row[0]),
        "ticket_id": str(row[1]),
        "customer_id": str(row[2]),
        "original_filename": str(row[3]),
        "content_type": str(row[4] or "application/octet-stream"),
        "size_bytes": int(row[5] or 0),
        "extension": str(row[6] or "").lower(),
        "status": _normalize_status(row[7]),
        "storage_provider": str(row[8] or "s3"),
        "bucket": str(row[9] or ""),
        "s3_key": str(row[10] or ""),
        "etag": str(row[11]).strip() if row[11] is not None else None,
        "checksum": str(row[12]).strip() if row[12] is not None else None,
        "meta": row[13] if isinstance(row[13], dict) else {},
        "created_at": _to_iso(row[14]),
        "updated_at": _to_iso(row[15]),
        "uploaded_at": _to_iso(row[16]) if row[16] is not None else None,
        "attached_at": _to_iso(row[17]) if row[17] is not None else None,
    }


class AssetRepository(Protocol):
    def initialize(self) -> None:
        ...

    def close(self) -> None:
        ...

    def storage_mode(self) -> str:
        ...

    def create_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        ...

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        ...

    def mark_uploaded(
        self,
        asset_id: str,
        *,
        size_bytes: int | None = None,
        etag: str | None = None,
        checksum: str | None = None,
    ) -> dict[str, Any] | None:
        ...

    def mark_attached(self, asset_ids: list[str]) -> list[dict[str, Any]]:
        ...

    def record_event(self, asset_id: str, event_type: str, payload: dict[str, Any]) -> None:
        ...


class InMemoryAssetRepository:
    def __init__(self) -> None:
        self._assets: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []

    def initialize(self) -> None:
        return None

    def close(self) -> None:
        return None

    def storage_mode(self) -> str:
        return "memory"

    def create_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id:
            raise ValueError("asset_id is required")
        now = _utc_now()
        record = copy.deepcopy(asset)
        record["asset_id"] = asset_id
        record["ticket_id"] = str(record.get("ticket_id") or "").strip()
        record["customer_id"] = str(record.get("customer_id") or "").strip()
        record["status"] = _normalize_status(record.get("status"))
        record["created_at"] = record.get("created_at") or now
        record["updated_at"] = record.get("updated_at") or now
        record.setdefault("uploaded_at", None)
        record.setdefault("attached_at", None)
        record.setdefault("etag", None)
        record.setdefault("checksum", None)
        record["meta"] = dict(record.get("meta")) if isinstance(record.get("meta"), dict) else {}
        self._assets[asset_id] = record
        self.record_event(asset_id, "created", {"status": record["status"]})
        return copy.deepcopy(record)

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        asset = self._assets.get(str(asset_id or "").strip())
        return copy.deepcopy(asset) if asset is not None else None

    def mark_uploaded(
        self,
        asset_id: str,
        *,
        size_bytes: int | None = None,
        etag: str | None = None,
        checksum: str | None = None,
    ) -> dict[str, Any] | None:
        asset = self._assets.get(str(asset_id or "").strip())
        if asset is None:
            return None
        asset["status"] = ASSET_STATUS_UPLOADED
        if size_bytes is not None and size_bytes > 0:
            asset["size_bytes"] = int(size_bytes)
        if etag is not None:
            asset["etag"] = str(etag)
        if checksum is not None:
            asset["checksum"] = str(checksum)
        asset["uploaded_at"] = _utc_now()
        asset["updated_at"] = asset["uploaded_at"]
        self.record_event(str(asset_id), "uploaded", {"size_bytes": asset.get("size_bytes")})
        return copy.deepcopy(asset)

    def mark_attached(self, asset_ids: list[str]) -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        now = _utc_now()
        for asset_id in asset_ids:
            asset = self._assets.get(str(asset_id or "").strip())
            if asset is None:
                continue
            asset["status"] = ASSET_STATUS_ATTACHED
            asset["attached_at"] = now
            asset["updated_at"] = now
            self.record_event(str(asset_id), "attached", {"ticket_id": asset.get("ticket_id")})
            updated.append(copy.deepcopy(asset))
        return updated

    def record_event(self, asset_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self._events.append(
            {
                "asset_id": str(asset_id or "").strip(),
                "event_type": str(event_type or "").strip(),
                "payload": copy.deepcopy(payload) if isinstance(payload, dict) else {},
                "created_at": _utc_now(),
            }
        )


class PostgresAssetRepository:
    def __init__(
        self,
        *,
        dsn: str,
        schema: str = "supportportal",
        pool_min_size: int = 1,
        pool_max_size: int = 8,
        pool_timeout_seconds: float = 15.0,
        migration_dsn: str | None = None,
    ) -> None:
        self._dsn = dsn
        self._migration_dsn = str(migration_dsn or dsn).strip()
        self._schema = schema
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool_timeout_seconds = pool_timeout_seconds
        self._pool: Any = None

    def _table(self, table_name: str) -> sql.Composed:
        return sql.SQL("{}.{}").format(sql.Identifier(self._schema), sql.Identifier(table_name))

    def _connect(self) -> Any:
        if ConnectionPool is not None:
            if self._pool is None:
                self._pool = ConnectionPool(
                    conninfo=self._dsn,
                    min_size=self._pool_min_size,
                    max_size=self._pool_max_size,
                    timeout=self._pool_timeout_seconds,
                    open=True,
                )
            return self._pool.connection()
        return psycopg.connect(self._dsn)

    def _connect_for_initialize(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self._migration_dsn)

    def initialize(self) -> None:
        with self._connect_for_initialize() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (842918, 2))
                    cur.execute(
                        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self._schema))
                    )
                    cur.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {} (
                                asset_id TEXT PRIMARY KEY,
                                ticket_id TEXT NOT NULL,
                                customer_id TEXT NOT NULL,
                                original_filename TEXT NOT NULL,
                                content_type TEXT NOT NULL,
                                size_bytes BIGINT NOT NULL,
                                extension TEXT NOT NULL,
                                status TEXT NOT NULL,
                                storage_provider TEXT NOT NULL,
                                bucket TEXT NOT NULL,
                                s3_key TEXT NOT NULL,
                                etag TEXT,
                                checksum TEXT,
                                meta JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                                created_at TIMESTAMPTZ NOT NULL,
                                updated_at TIMESTAMPTZ NOT NULL,
                                uploaded_at TIMESTAMPTZ,
                                attached_at TIMESTAMPTZ
                            )
                            """
                        ).format(self._table("support_assets"))
                    )
                    cur.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {} (
                                id BIGSERIAL PRIMARY KEY,
                                asset_id TEXT NOT NULL REFERENCES {}(asset_id) ON DELETE CASCADE,
                                event_type TEXT NOT NULL,
                                payload JSONB NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                            )
                            """
                        ).format(
                            self._table("support_asset_events"),
                            self._table("support_assets"),
                        )
                    )
                    cur.execute(
                        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (ticket_id, customer_id)").format(
                            sql.Identifier("idx_support_assets_ticket_customer"),
                            self._table("support_assets"),
                        )
                    )
                    cur.execute(
                        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (asset_id, created_at DESC)").format(
                            sql.Identifier("idx_support_asset_events_asset_created"),
                            self._table("support_asset_events"),
                        )
                    )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def storage_mode(self) -> str:
        return "postgres"

    def create_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        now = asset.get("created_at") or _utc_now()
        record = copy.deepcopy(asset)
        record["status"] = _normalize_status(record.get("status"))

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                asset_id,
                                ticket_id,
                                customer_id,
                                original_filename,
                                content_type,
                                size_bytes,
                                extension,
                                status,
                                storage_provider,
                                bucket,
                                s3_key,
                                etag,
                                checksum,
                                meta,
                                created_at,
                                updated_at,
                                uploaded_at,
                                attached_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING asset_id, ticket_id, customer_id, original_filename, content_type,
                                      size_bytes, extension, status, storage_provider, bucket, s3_key,
                                      etag, checksum, meta, created_at, updated_at, uploaded_at, attached_at
                            """
                        ).format(self._table("support_assets")),
                        (
                            str(record.get("asset_id") or "").strip(),
                            str(record.get("ticket_id") or "").strip(),
                            str(record.get("customer_id") or "").strip(),
                            str(record.get("original_filename") or "").strip(),
                            str(record.get("content_type") or "application/octet-stream").strip(),
                            int(record.get("size_bytes") or 0),
                            str(record.get("extension") or "").strip().lower(),
                            record["status"],
                            str(record.get("storage_provider") or "s3").strip(),
                            str(record.get("bucket") or "").strip(),
                            str(record.get("s3_key") or "").strip(),
                            record.get("etag"),
                            record.get("checksum"),
                            Json(record.get("meta") if isinstance(record.get("meta"), dict) else {}),
                            now,
                            record.get("updated_at") or now,
                            record.get("uploaded_at"),
                            record.get("attached_at"),
                        ),
                    )
                    row = cur.fetchone()
                    payload = _asset_row_to_payload(row)
                    self._record_event_with_cursor(cur, payload["asset_id"], "created", {"status": payload["status"]})
                    return payload

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT asset_id, ticket_id, customer_id, original_filename, content_type,
                               size_bytes, extension, status, storage_provider, bucket, s3_key,
                               etag, checksum, meta, created_at, updated_at, uploaded_at, attached_at
                        FROM {}
                        WHERE asset_id = %s
                        """
                    ).format(self._table("support_assets")),
                    (str(asset_id or "").strip(),),
                )
                row = cur.fetchone()
        return _asset_row_to_payload(row) if row is not None else None

    def mark_uploaded(
        self,
        asset_id: str,
        *,
        size_bytes: int | None = None,
        etag: str | None = None,
        checksum: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET status = %s,
                                size_bytes = COALESCE(%s, size_bytes),
                                etag = COALESCE(%s, etag),
                                checksum = COALESCE(%s, checksum),
                                uploaded_at = %s,
                                updated_at = %s
                            WHERE asset_id = %s
                            RETURNING asset_id, ticket_id, customer_id, original_filename, content_type,
                                      size_bytes, extension, status, storage_provider, bucket, s3_key,
                                      etag, checksum, meta, created_at, updated_at, uploaded_at, attached_at
                            """
                        ).format(self._table("support_assets")),
                        (
                            ASSET_STATUS_UPLOADED,
                            int(size_bytes) if size_bytes is not None and size_bytes > 0 else None,
                            etag,
                            checksum,
                            now,
                            now,
                            str(asset_id or "").strip(),
                        ),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return None
                    payload = _asset_row_to_payload(row)
                    self._record_event_with_cursor(cur, payload["asset_id"], "uploaded", {"size_bytes": payload["size_bytes"]})
                    return payload

    def mark_attached(self, asset_ids: list[str]) -> list[dict[str, Any]]:
        normalized_ids = [str(asset_id or "").strip() for asset_id in asset_ids if str(asset_id or "").strip()]
        if not normalized_ids:
            return []
        now = _utc_now()
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET status = %s, attached_at = %s, updated_at = %s
                            WHERE asset_id = ANY(%s)
                            RETURNING asset_id, ticket_id, customer_id, original_filename, content_type,
                                      size_bytes, extension, status, storage_provider, bucket, s3_key,
                                      etag, checksum, meta, created_at, updated_at, uploaded_at, attached_at
                            """
                        ).format(self._table("support_assets")),
                        (ASSET_STATUS_ATTACHED, now, now, normalized_ids),
                    )
                    rows = cur.fetchall()
                    payloads = [_asset_row_to_payload(row) for row in rows]
                    for payload in payloads:
                        self._record_event_with_cursor(
                            cur,
                            payload["asset_id"],
                            "attached",
                            {"ticket_id": payload["ticket_id"]},
                        )
                    return payloads

    def _record_event_with_cursor(
        self,
        cur: psycopg.Cursor[Any],
        asset_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {} (asset_id, event_type, payload)
                VALUES (%s, %s, %s)
                """
            ).format(self._table("support_asset_events")),
            (str(asset_id or "").strip(), str(event_type or "").strip(), Json(payload)),
        )

    def record_event(self, asset_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    self._record_event_with_cursor(cur, asset_id, event_type, payload)


def create_asset_repository() -> AssetRepository:
    dsn = (os.getenv("TICKET_DB_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("TICKET_DB_DSN is required")
    schema = (os.getenv("TICKET_DB_SCHEMA") or "supportportal").strip() or "supportportal"
    return PostgresAssetRepository(
        dsn=dsn,
        schema=schema,
        pool_min_size=_safe_positive_int(os.getenv("TICKET_DB_POOL_MIN_SIZE"), 1),
        pool_max_size=_safe_positive_int(os.getenv("TICKET_DB_POOL_MAX_SIZE"), 8),
        pool_timeout_seconds=_safe_positive_float(os.getenv("TICKET_DB_POOL_TIMEOUT_SECONDS"), 15.0),
        migration_dsn=(os.getenv("TICKET_DB_MIGRATION_DSN") or "").strip() or None,
    )

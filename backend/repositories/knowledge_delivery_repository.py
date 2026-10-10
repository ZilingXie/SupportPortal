"""Repository layer for per-target knowledge deliveries (dual-write phase 2).

One delivery row per (candidate promotion, target).  AgentMemory and WeKnora
never share a success state: each target carries its own lease state machine
(queued/active/accepted/failed/outcome_unknown/invalidated), external object
identity, idempotency key, request receipt, readback result, and failure
classification.  Candidate-level resolution (both targets accepted, or one
side failed -> human review) is derived from the delivery rows, never from a
shared status.
"""

from __future__ import annotations

import copy
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Json

KNOWLEDGE_DELIVERY_TARGETS = ("agent_memory", "weknora")
KNOWLEDGE_DELIVERY_STATUSES = (
    "queued",
    "active",
    "accepted",
    "failed",
    "outcome_unknown",
    "invalidated",
)
KNOWLEDGE_DELIVERY_FIELDS = (
    "delivery_id",
    "promotion_id",
    "target",
    "status",
    "external_object_id",
    "external_version",
    "idempotency_key",
    "operation_receipt",
    "readback_result",
    "failure_code",
    "failure_detail",
    "attempt_count",
    "owner_token",
    "claimed_at",
    "lease_expires_at",
    "created_at",
    "updated_at",
)

_KNOWLEDGE_DELIVERY_TABLE = "support_knowledge_deliveries"


def knowledge_delivery_id(*, promotion_id: str, target: str) -> str:
    return f"{str(promotion_id or '')}:{str(target or '')}"


def normalize_knowledge_delivery(
    *, promotion_id: str, target: str, now_value: str
) -> dict[str, Any]:
    promotion_id = str(promotion_id or "").strip()
    target = str(target or "").strip()
    if not promotion_id:
        raise ValueError("knowledge delivery requires promotion_id")
    if target not in KNOWLEDGE_DELIVERY_TARGETS:
        raise ValueError(f"knowledge delivery target must be one of {KNOWLEDGE_DELIVERY_TARGETS}")
    delivery_id = knowledge_delivery_id(promotion_id=promotion_id, target=target)
    return {
        "delivery_id": delivery_id,
        "promotion_id": promotion_id,
        "target": target,
        "status": "queued",
        "external_object_id": None,
        "external_version": None,
        "idempotency_key": delivery_id,
        "operation_receipt": {},
        "readback_result": None,
        "failure_code": None,
        "failure_detail": None,
        "attempt_count": 0,
        "owner_token": None,
        "claimed_at": None,
        "lease_expires_at": None,
        "created_at": now_value,
        "updated_at": now_value,
    }


class InMemoryKnowledgeDeliveryRepositoryMixin:
    def _knowledge_delivery_state(self) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_knowledge_deliveries"):
            self._knowledge_deliveries: dict[str, dict[str, Any]] = {}
        return self._knowledge_deliveries

    def ensure_knowledge_deliveries(
        self, promotion_id: str, targets: list[str], *, now_value: str
    ) -> list[dict[str, Any]]:
        with self._assignment_lock:
            state = self._knowledge_delivery_state()
            for target in targets:
                normalized = normalize_knowledge_delivery(
                    promotion_id=promotion_id, target=target, now_value=now_value
                )
                state.setdefault(normalized["delivery_id"], normalized)
            return [
                copy.deepcopy(row)
                for key, row in state.items()
                if row["promotion_id"] == str(promotion_id)
            ]

    def list_knowledge_deliveries(self, promotion_id: str | None = None) -> list[dict[str, Any]]:
        with self._assignment_lock:
            rows = [
                copy.deepcopy(row)
                for row in self._knowledge_delivery_state().values()
                if promotion_id is None or row["promotion_id"] == str(promotion_id)
            ]
            return sorted(rows, key=lambda item: (item["created_at"], item["delivery_id"]))

    def claim_knowledge_delivery(
        self, delivery_id: str, *, owner_token: str, claimed_at: str, lease_expires_at: str
    ) -> dict[str, Any] | None:
        with self._assignment_lock:
            row = self._knowledge_delivery_state().get(str(delivery_id))
            if row is None:
                return None
            if row["status"] not in {"queued", "outcome_unknown", "active"}:
                return None
            if row["status"] == "active" and str(row.get("lease_expires_at") or "") > claimed_at:
                return None
            row.update(
                status="active",
                owner_token=owner_token,
                claimed_at=claimed_at,
                lease_expires_at=lease_expires_at,
                attempt_count=int(row.get("attempt_count") or 0) + 1,
                updated_at=claimed_at,
            )
            return copy.deepcopy(row)

    def mark_knowledge_delivery_receipt(
        self, delivery_id: str, receipt_patch: dict[str, Any], *, owner_token: str, updated_at: str
    ) -> dict[str, Any] | None:
        """Merge ``receipt_patch`` into the in-flight operation receipt.

        Used for the AgentMemory create-attempt marker: the flag must be
        durable BEFORE the create request fires so a timeout can never be
        mistaken for "never attempted" on retry.
        """
        with self._assignment_lock:
            row = self._knowledge_delivery_state().get(str(delivery_id))
            if row is None or row.get("owner_token") != owner_token:
                return None
            receipt = row.get("operation_receipt")
            receipt = copy.deepcopy(receipt) if isinstance(receipt, dict) else {}
            receipt.update(copy.deepcopy(receipt_patch))
            row.update(operation_receipt=receipt, updated_at=updated_at)
            return copy.deepcopy(row)

    def set_knowledge_delivery_external_object(
        self, delivery_id: str, external_object_id: str, *, owner_token: str, updated_at: str
    ) -> dict[str, Any] | None:
        with self._assignment_lock:
            row = self._knowledge_delivery_state().get(str(delivery_id))
            if row is None or row.get("owner_token") != owner_token:
                return None
            row.update(
                external_object_id=str(external_object_id or "") or None,
                updated_at=updated_at,
            )
            return copy.deepcopy(row)

    def complete_knowledge_delivery(
        self,
        delivery_id: str,
        *,
        owner_token: str,
        status: str,
        external_object_id: str | None = None,
        external_version: str | None = None,
        receipt: dict[str, Any] | None = None,
        readback: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_detail: str | None = None,
        completed_at: str,
    ) -> dict[str, Any]:
        if status not in {"accepted", "failed", "outcome_unknown", "invalidated"}:
            raise ValueError("invalid knowledge delivery completion status")
        with self._assignment_lock:
            row = self._knowledge_delivery_state().get(str(delivery_id))
            if row is None or row.get("owner_token") != owner_token:
                raise RuntimeError("stale knowledge delivery completion")
            if row["status"] != "active":
                # Late receipt (mirrors the promotion contract): the external
                # effect may already have happened when the candidate was
                # invalidated.  Record the evidence, keep the terminal state.
                if row["status"] != "invalidated":
                    raise RuntimeError("stale knowledge delivery completion")
                row.update(
                    external_object_id=external_object_id or row.get("external_object_id"),
                    external_version=external_version or row.get("external_version"),
                    operation_receipt=copy.deepcopy(receipt)
                    if receipt is not None
                    else row.get("operation_receipt"),
                    readback_result=copy.deepcopy(readback) if readback is not None else row.get("readback_result"),
                    failure_code=failure_code,
                    failure_detail=failure_detail,
                    updated_at=completed_at,
                )
                return {**copy.deepcopy(row), "late_receipt_recorded": True}
            row.update(
                status=status,
                external_object_id=external_object_id or row.get("external_object_id"),
                external_version=external_version or row.get("external_version"),
                operation_receipt=copy.deepcopy(receipt)
                if receipt is not None
                else row.get("operation_receipt"),
                readback_result=copy.deepcopy(readback) if readback is not None else None,
                failure_code=failure_code,
                failure_detail=failure_detail,
                lease_expires_at=None,
                updated_at=completed_at,
            )
            return copy.deepcopy(row)

    # ------------------------------------------- candidate-level transitions

    def mark_knowledge_candidate_delivering(self, promotion_id: str, *, now_value: str) -> dict[str, Any] | None:
        state = getattr(self, "_weknora_promotion_state", None)
        if not callable(state):
            return None
        with self._assignment_lock:
            row = state().get(str(promotion_id))
            if row is None or row["status"] != "queued":
                return None
            row.update(status="active", updated_at=now_value)
            return copy.deepcopy(row)

    def park_knowledge_candidate(
        self, promotion_id: str, *, reasons: list[str], now_value: str
    ) -> dict[str, Any] | None:
        state = getattr(self, "_weknora_promotion_state", None)
        if not callable(state):
            return None
        with self._assignment_lock:
            row = state().get(str(promotion_id))
            if row is None or row["status"] not in {"queued", "active"}:
                return None
            row.update(
                status="human_review",
                failure_code="dual_write_gate",
                failure_detail=" | ".join(str(reason) for reason in reasons)[:2000],
                lease_expires_at=None,
                updated_at=now_value,
            )
            return copy.deepcopy(row)

    def resolve_knowledge_candidate_noop(self, promotion_id: str, *, now_value: str) -> dict[str, Any] | None:
        state = getattr(self, "_weknora_promotion_state", None)
        if not callable(state):
            return None
        with self._assignment_lock:
            row = state().get(str(promotion_id))
            if row is None or row["status"] != "queued":
                return None
            row.update(
                status="accepted",
                operation_receipt={"no_change": True, "zero_writes": True},
                lease_expires_at=None,
                updated_at=now_value,
            )
            return copy.deepcopy(row)

    def resolve_knowledge_candidate_accepted(
        self,
        promotion_id: str,
        *,
        weknora_object_id: str | None,
        weknora_version: str | None,
        receipt: dict[str, Any] | None,
        now_value: str,
    ) -> dict[str, Any] | None:
        state = getattr(self, "_weknora_promotion_state", None)
        if not callable(state):
            return None
        with self._assignment_lock:
            row = state().get(str(promotion_id))
            if row is None or row["status"] != "active":
                return None
            row.update(
                status="accepted",
                weknora_object_id=weknora_object_id,
                weknora_version=weknora_version,
                operation_receipt=copy.deepcopy(receipt) if receipt is not None else row.get("operation_receipt"),
                failure_code=None,
                failure_detail=None,
                lease_expires_at=None,
                updated_at=now_value,
            )
            return copy.deepcopy(row)

    def requeue_knowledge_delivery(
        self, delivery_id: str, *, requeued_at: str, reason: str
    ) -> dict[str, Any] | None:
        with self._assignment_lock:
            row = self._knowledge_delivery_state().get(str(delivery_id))
            if row is None or row["status"] not in {"failed", "outcome_unknown"}:
                return None
            row.update(
                status="queued",
                owner_token=None,
                claimed_at=None,
                lease_expires_at=None,
                failure_detail=f"requeued: {reason}",
                updated_at=requeued_at,
            )
            return copy.deepcopy(row)

    def _requeue_candidate_deliveries_locked(
        self, promotion_id: str, *, requeued_at: str, reason: str
    ) -> int:
        """Requeue the FAILED/UNKNOWN targets of one candidate (approve repair).

        Accepted targets are never touched — "一个目标成功、另一个目标失败时，
        只补失败目标".
        """
        repaired = 0
        for row in self._knowledge_delivery_state().values():
            if row["promotion_id"] != str(promotion_id):
                continue
            if row["status"] in {"failed", "outcome_unknown"}:
                row.update(
                    status="queued",
                    owner_token=None,
                    claimed_at=None,
                    lease_expires_at=None,
                    failure_detail=f"requeued: {reason}",
                    updated_at=requeued_at,
                )
                repaired += 1
        return repaired

    def _invalidate_candidate_deliveries_locked(self, promotion_id: str, *, invalidated_at: str) -> int:
        invalidated = 0
        for row in self._knowledge_delivery_state().values():
            if row["promotion_id"] != str(promotion_id):
                continue
            if row["status"] in {"queued", "active", "failed", "outcome_unknown"}:
                row.update(status="invalidated", lease_expires_at=None, updated_at=invalidated_at)
                invalidated += 1
        return invalidated


class PostgresKnowledgeDeliveryRepositoryMixin:
    def _initialize_knowledge_delivery_schema(self, cur: psycopg.Cursor[Any]) -> None:
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    delivery_id TEXT PRIMARY KEY,
                    promotion_id TEXT NOT NULL REFERENCES {}(promotion_id) ON DELETE CASCADE,
                    target TEXT NOT NULL CHECK (target IN ('agent_memory','weknora')),
                    status TEXT NOT NULL CHECK (status IN (
                        'queued','active','accepted','failed','outcome_unknown','invalidated'
                    )),
                    external_object_id TEXT, external_version TEXT,
                    idempotency_key TEXT NOT NULL,
                    operation_receipt JSONB, readback_result JSONB,
                    failure_code TEXT, failure_detail TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    owner_token TEXT, claimed_at TIMESTAMPTZ, lease_expires_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
                )
                """
            ).format(
                self._table(_KNOWLEDGE_DELIVERY_TABLE),
                self._table("support_weknora_promotions"),
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, created_at, delivery_id)").format(
                sql.Identifier("idx_support_knowledge_deliveries_claim"),
                self._table(_KNOWLEDGE_DELIVERY_TABLE),
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (promotion_id)").format(
                sql.Identifier("idx_support_knowledge_deliveries_promotion"),
                self._table(_KNOWLEDGE_DELIVERY_TABLE),
            )
        )

    def _delivery_row(self, record: tuple[Any, ...]) -> dict[str, Any]:
        row = dict(zip(KNOWLEDGE_DELIVERY_FIELDS, record))
        row["operation_receipt"] = _json_value(row.get("operation_receipt"))
        row["readback_result"] = _json_value(row.get("readback_result"))
        for field in ("created_at", "updated_at", "claimed_at", "lease_expires_at"):
            row[field] = _iso(row.get(field))
        return row

    def ensure_knowledge_deliveries(
        self, promotion_id: str, targets: list[str], *, now_value: str
    ) -> list[dict[str, Any]]:
        normalized = [
            normalize_knowledge_delivery(promotion_id=promotion_id, target=target, now_value=now_value)
            for target in targets
        ]

        def operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.transaction(), conn.cursor() as cur:
                for item in normalized:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (delivery_id, promotion_id, target, status,
                                idempotency_key, operation_receipt, attempt_count, created_at, updated_at)
                            VALUES (%s,%s,%s,'queued',%s,%s,0,%s,%s)
                            ON CONFLICT (delivery_id) DO NOTHING
                            """
                        ).format(self._table(_KNOWLEDGE_DELIVERY_TABLE)),
                        (
                            item["delivery_id"],
                            item["promotion_id"],
                            item["target"],
                            item["idempotency_key"],
                            Json(item["operation_receipt"]),
                            now_value,
                            now_value,
                        ),
                    )
                cur.execute(
                    sql.SQL("SELECT {} FROM {} WHERE promotion_id=%s ORDER BY created_at, delivery_id").format(
                        sql.SQL(",").join(map(sql.Identifier, KNOWLEDGE_DELIVERY_FIELDS)),
                        self._table(_KNOWLEDGE_DELIVERY_TABLE),
                    ),
                    (str(promotion_id),),
                )
                return [self._delivery_row(record) for record in cur.fetchall()]

        return self._run_with_connection_retry("ensure_knowledge_deliveries", operation)

    def list_knowledge_deliveries(self, promotion_id: str | None = None) -> list[dict[str, Any]]:
        def operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                if promotion_id is None:
                    cur.execute(
                        sql.SQL("SELECT {} FROM {} ORDER BY created_at, delivery_id").format(
                            sql.SQL(",").join(map(sql.Identifier, KNOWLEDGE_DELIVERY_FIELDS)),
                            self._table(_KNOWLEDGE_DELIVERY_TABLE),
                        )
                    )
                else:
                    cur.execute(
                        sql.SQL("SELECT {} FROM {} WHERE promotion_id=%s ORDER BY created_at, delivery_id").format(
                            sql.SQL(",").join(map(sql.Identifier, KNOWLEDGE_DELIVERY_FIELDS)),
                            self._table(_KNOWLEDGE_DELIVERY_TABLE),
                        ),
                        (str(promotion_id),),
                    )
                return [self._delivery_row(record) for record in cur.fetchall()]

        return self._run_with_connection_retry("list_knowledge_deliveries", operation)

    def claim_knowledge_delivery(
        self, delivery_id: str, *, owner_token: str, claimed_at: str, lease_expires_at: str
    ) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status='active', owner_token=%s, claimed_at=%s,
                        lease_expires_at=%s, attempt_count=attempt_count+1, updated_at=%s
                        WHERE delivery_id=%s
                        AND (status IN ('queued','outcome_unknown')
                             OR (status='active' AND lease_expires_at<=%s))
                        RETURNING {}
                        """
                    ).format(
                        self._table(_KNOWLEDGE_DELIVERY_TABLE),
                        sql.SQL(",").join(map(sql.Identifier, KNOWLEDGE_DELIVERY_FIELDS)),
                    ),
                    (owner_token, claimed_at, lease_expires_at, claimed_at, str(delivery_id), claimed_at),
                )
                record = cur.fetchone()
                return None if record is None else self._delivery_row(record)

        return self._run_with_connection_retry("claim_knowledge_delivery", operation)

    def mark_knowledge_delivery_receipt(
        self, delivery_id: str, receipt_patch: dict[str, Any], *, owner_token: str, updated_at: str
    ) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET operation_receipt = COALESCE(operation_receipt, '{}'::jsonb) || %s,
                        updated_at=%s
                        WHERE delivery_id=%s AND owner_token=%s
                        RETURNING {}
                        """
                    ).format(
                        self._table(_KNOWLEDGE_DELIVERY_TABLE),
                        sql.SQL(",").join(map(sql.Identifier, KNOWLEDGE_DELIVERY_FIELDS)),
                    ),
                    (Json(receipt_patch), updated_at, str(delivery_id), owner_token),
                )
                record = cur.fetchone()
                return None if record is None else self._delivery_row(record)

        return self._run_with_connection_retry("mark_knowledge_delivery_receipt", operation)

    def set_knowledge_delivery_external_object(
        self, delivery_id: str, external_object_id: str, *, owner_token: str, updated_at: str
    ) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET external_object_id=%s, updated_at=%s
                        WHERE delivery_id=%s AND owner_token=%s
                        RETURNING {}
                        """
                    ).format(
                        self._table(_KNOWLEDGE_DELIVERY_TABLE),
                        sql.SQL(",").join(map(sql.Identifier, KNOWLEDGE_DELIVERY_FIELDS)),
                    ),
                    (str(external_object_id or "") or None, updated_at, str(delivery_id), owner_token),
                )
                record = cur.fetchone()
                return None if record is None else self._delivery_row(record)

        return self._run_with_connection_retry("set_knowledge_delivery_external_object", operation)

    def complete_knowledge_delivery(
        self,
        delivery_id: str,
        *,
        owner_token: str,
        status: str,
        external_object_id: str | None = None,
        external_version: str | None = None,
        receipt: dict[str, Any] | None = None,
        readback: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_detail: str | None = None,
        completed_at: str,
    ) -> dict[str, Any]:
        if status not in {"accepted", "failed", "outcome_unknown", "invalidated"}:
            raise ValueError("invalid knowledge delivery completion status")

        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status=%s,
                        external_object_id=COALESCE(%s, external_object_id),
                        external_version=COALESCE(%s, external_version),
                        operation_receipt=%s, readback_result=%s,
                        failure_code=%s, failure_detail=%s,
                        lease_expires_at=NULL, updated_at=%s
                        WHERE delivery_id=%s AND status='active' AND owner_token=%s
                        RETURNING delivery_id, status
                        """
                    ).format(self._table(_KNOWLEDGE_DELIVERY_TABLE)),
                    (
                        status,
                        external_object_id,
                        external_version,
                        Json(receipt) if receipt is not None else None,
                        Json(readback) if readback is not None else None,
                        failure_code,
                        failure_detail,
                        completed_at,
                        str(delivery_id),
                        owner_token,
                    ),
                )
                record = cur.fetchone()
                if record is None:
                    # Late receipt to an invalidated delivery: keep evidence.
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {} SET
                            external_object_id=COALESCE(%s, external_object_id),
                            external_version=COALESCE(%s, external_version),
                            operation_receipt=COALESCE(%s, operation_receipt),
                            readback_result=COALESCE(%s, readback_result),
                            failure_code=%s, failure_detail=%s, updated_at=%s
                            WHERE delivery_id=%s AND owner_token=%s AND status='invalidated'
                            RETURNING status
                            """
                        ).format(self._table(_KNOWLEDGE_DELIVERY_TABLE)),
                        (
                            external_object_id,
                            external_version,
                            Json(receipt) if receipt is not None else None,
                            Json(readback) if readback is not None else None,
                            failure_code,
                            failure_detail,
                            completed_at,
                            str(delivery_id),
                            owner_token,
                        ),
                    )
                    late = cur.fetchone()
                    if late is None:
                        raise RuntimeError("stale knowledge delivery completion")
                    return {
                        "delivery_id": str(delivery_id),
                        "status": str(late[0]),
                        "late_receipt_recorded": True,
                    }
                return {"delivery_id": str(delivery_id), "status": str(record[1])}

        return self._run_with_connection_retry("complete_knowledge_delivery", operation)

    def requeue_knowledge_delivery(
        self, delivery_id: str, *, requeued_at: str, reason: str
    ) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status='queued', owner_token=NULL, claimed_at=NULL,
                        lease_expires_at=NULL, failure_detail=%s, updated_at=%s
                        WHERE delivery_id=%s AND status IN ('failed','outcome_unknown')
                        RETURNING delivery_id
                        """
                    ).format(self._table(_KNOWLEDGE_DELIVERY_TABLE)),
                    (f"requeued: {reason}", requeued_at, str(delivery_id)),
                )
                record = cur.fetchone()
                return None if record is None else {"delivery_id": str(delivery_id), "status": "queued"}

        return self._run_with_connection_retry("requeue_knowledge_delivery", operation)

    def _requeue_candidate_deliveries_locked(
        self, promotion_id: str, *, requeued_at: str, reason: str
    ) -> int:
        def operation(conn: psycopg.Connection[Any]) -> int:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status='queued', owner_token=NULL, claimed_at=NULL,
                        lease_expires_at=NULL, failure_detail=%s, updated_at=%s
                        WHERE promotion_id=%s AND status IN ('failed','outcome_unknown')
                        """
                    ).format(self._table(_KNOWLEDGE_DELIVERY_TABLE)),
                    (f"requeued: {reason}", requeued_at, str(promotion_id)),
                )
                return cur.rowcount

        return self._run_with_connection_retry("_requeue_candidate_deliveries_locked", operation)

    def _invalidate_candidate_deliveries_locked(self, promotion_id: str, *, invalidated_at: str) -> int:
        def operation(conn: psycopg.Connection[Any]) -> int:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status='invalidated', lease_expires_at=NULL, updated_at=%s
                        WHERE promotion_id=%s
                        AND status IN ('queued','active','failed','outcome_unknown')
                        """
                    ).format(self._table(_KNOWLEDGE_DELIVERY_TABLE)),
                    (invalidated_at, str(promotion_id)),
                )
                return cur.rowcount

        return self._run_with_connection_retry("_invalidate_candidate_deliveries_locked", operation)

    # ------------------------------------------- candidate-level transitions

    _PROMOTION_TABLE_NAME = "support_weknora_promotions"

    def mark_knowledge_candidate_delivering(self, promotion_id: str, *, now_value: str) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status='active', updated_at=%s "
                        "WHERE promotion_id=%s AND status='queued' RETURNING promotion_id, status"
                    ).format(self._table(self._PROMOTION_TABLE_NAME)),
                    (now_value, str(promotion_id)),
                )
                record = cur.fetchone()
                return None if record is None else {"promotion_id": str(record[0]), "status": str(record[1])}

        return self._run_with_connection_retry("mark_knowledge_candidate_delivering", operation)

    def park_knowledge_candidate(
        self, promotion_id: str, *, reasons: list[str], now_value: str
    ) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status='human_review', failure_code='dual_write_gate', "
                        "failure_detail=%s, lease_expires_at=NULL, updated_at=%s "
                        "WHERE promotion_id=%s AND status IN ('queued','active') "
                        "RETURNING promotion_id, status"
                    ).format(self._table(self._PROMOTION_TABLE_NAME)),
                    (" | ".join(str(reason) for reason in reasons)[:2000], now_value, str(promotion_id)),
                )
                record = cur.fetchone()
                return None if record is None else {"promotion_id": str(record[0]), "status": str(record[1])}

        return self._run_with_connection_retry("park_knowledge_candidate", operation)

    def resolve_knowledge_candidate_noop(self, promotion_id: str, *, now_value: str) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status='accepted', operation_receipt=%s, "
                        "lease_expires_at=NULL, updated_at=%s "
                        "WHERE promotion_id=%s AND status='queued' "
                        "RETURNING promotion_id, status"
                    ).format(self._table(self._PROMOTION_TABLE_NAME)),
                    (
                        Json({"no_change": True, "zero_writes": True}),
                        now_value,
                        str(promotion_id),
                    ),
                )
                record = cur.fetchone()
                return None if record is None else {"promotion_id": str(record[0]), "status": str(record[1])}

        return self._run_with_connection_retry("resolve_knowledge_candidate_noop", operation)

    def resolve_knowledge_candidate_accepted(
        self,
        promotion_id: str,
        *,
        weknora_object_id: str | None,
        weknora_version: str | None,
        receipt: dict[str, Any] | None,
        now_value: str,
    ) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status='accepted', weknora_object_id=%s, weknora_version=%s, "
                        "operation_receipt=%s, failure_code=NULL, failure_detail=NULL, "
                        "lease_expires_at=NULL, updated_at=%s "
                        "WHERE promotion_id=%s AND status='active' "
                        "RETURNING promotion_id, status"
                    ).format(self._table(self._PROMOTION_TABLE_NAME)),
                    (
                        weknora_object_id,
                        weknora_version,
                        Json(receipt) if receipt is not None else None,
                        now_value,
                        str(promotion_id),
                    ),
                )
                record = cur.fetchone()
                return None if record is None else {"promotion_id": str(record[0]), "status": str(record[1])}

        return self._run_with_connection_retry("resolve_knowledge_candidate_accepted", operation)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    import json

    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


__all__ = [
    "KNOWLEDGE_DELIVERY_FIELDS",
    "KNOWLEDGE_DELIVERY_STATUSES",
    "KNOWLEDGE_DELIVERY_TARGETS",
    "InMemoryKnowledgeDeliveryRepositoryMixin",
    "PostgresKnowledgeDeliveryRepositoryMixin",
    "knowledge_delivery_id",
    "normalize_knowledge_delivery",
]

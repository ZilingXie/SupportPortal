"""Repository layer for WeKnora promotion tasks (lineage + lease state).

Stores cross-system lineage (case, ticket, summary/review sessions, Slack
thread) and the worker state machine (queued/active/accepted/failed/
outcome_unknown/human_review/invalidated) in SupportPortal PostgreSQL.  A
unique constraint on (source_type, source_id, source_version, candidate_type)
guarantees one WeKnora object per source version.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Json

WEKNORA_PROMOTION_STATUSES = (
    "queued",
    "active",
    "accepted",
    "failed",
    "outcome_unknown",
    "human_review",
    "invalidated",
)
WEKNORA_PROMOTION_FIELDS = (
    "promotion_id",
    "engineer_case_id",
    "client_ticket_id",
    "investigation_id",
    "summary_session_id",
    "summary_run_id",
    "review_session_id",
    "review_run_id",
    "slack_channel_id",
    "slack_thread_ts",
    "source_type",
    "source_id",
    "source_version",
    "content_hash",
    "candidate_type",
    "decision",
    "candidate_payload",
    "status",
    "owner_token",
    "claimed_at",
    "lease_expires_at",
    "attempt_count",
    "weknora_object_id",
    "weknora_version",
    "operation_receipt",
    "failure_code",
    "failure_detail",
    "human_decision",
    "human_decision_detail",
    "human_decided_at",
    "input_fingerprint",
    "created_at",
    "updated_at",
)


def weknora_promotion_id(
    *,
    source_type: str,
    source_id: str,
    source_version: str,
    candidate_type: str,
    content_hash: str,
) -> str:
    """Stable per-candidate identity.

    The candidate content hash discriminates same-type candidates from one
    source version (two different knowledge entries both survive), while a
    replayed event produces the same hash and therefore the same id.
    """
    return (
        f"weknora:{source_type}:{source_id}:{source_version}:{candidate_type}:{content_hash}"
    )


def normalize_weknora_promotion_task(task: dict[str, Any], *, now_value: str) -> dict[str, Any]:
    promotion_id = weknora_promotion_id(
        source_type=str(task.get("source_type") or ""),
        source_id=str(task.get("source_id") or ""),
        source_version=str(task.get("source_version") or ""),
        candidate_type=str(task.get("candidate_type") or ""),
        content_hash=str(task.get("content_hash") or ""),
    )
    normalized = {
        "promotion_id": promotion_id,
        "engineer_case_id": str(task.get("engineer_case_id") or ""),
        "client_ticket_id": str(task.get("client_ticket_id") or ""),
        "investigation_id": str(task.get("investigation_id") or "") or None,
        "summary_session_id": str(task.get("summary_session_id") or "") or None,
        "summary_run_id": str(task.get("summary_run_id") or "") or None,
        "review_session_id": str(task.get("review_session_id") or "") or None,
        "review_run_id": str(task.get("review_run_id") or "") or None,
        "slack_channel_id": str(task.get("slack_channel_id") or "") or None,
        "slack_thread_ts": str(task.get("slack_thread_ts") or "") or None,
        "source_type": str(task.get("source_type") or ""),
        "source_id": str(task.get("source_id") or ""),
        "source_version": str(task.get("source_version") or ""),
        "content_hash": str(task.get("content_hash") or ""),
        "candidate_type": str(task.get("candidate_type") or ""),
        "decision": str(task.get("decision") or ""),
        "candidate_payload": copy.deepcopy(task.get("candidate_payload") or {}),
        "status": "queued",
        "owner_token": None,
        "claimed_at": None,
        "lease_expires_at": None,
        "attempt_count": 0,
        "weknora_object_id": None,
        "weknora_version": None,
        "operation_receipt": None,
        "failure_code": None,
        "failure_detail": None,
        "human_decision": None,
        "human_decision_detail": None,
        "human_decided_at": None,
        # Frozen-input generation this promotion was produced from (review
        # round 3, R3-6); standalone promotions carry the source version.
        "input_fingerprint": str(task.get("input_fingerprint") or "") or None,
        "created_at": now_value,
        "updated_at": now_value,
    }
    required_lineage = (
        # Standalone (case-less) sources carry no engineer case; the source
        # identity is the lineage.
        ("source_type", "source_id", "source_version", "content_hash")
        if normalized["source_type"] == "knowledge_source_review"
        else (
            "engineer_case_id", "client_ticket_id",
            "source_type", "source_id", "source_version", "content_hash",
        )
    )
    missing = [field for field in required_lineage if not normalized[field]]
    if normalized["candidate_type"] not in {"knowledge", "memory", "skill"}:
        missing.append("candidate_type")
    elif normalized["candidate_type"] == "skill" and normalized["decision"] not in {
        "no_change",
        "human_review",
    }:
        # Skills are human-maintained: a skill proposal may only enter the
        # promotion pipeline as an explicit human-review (or no-op) record,
        # never as a write intent.
        raise ValueError(
            "WeKnora promotion skill candidates must be routed to human_review, "
            f"got decision={normalized['decision']!r}"
        )
    if normalized["decision"] not in {
        "no_change",
        "new",
        "supplement",
        "replace",
        "merge",
        "human_review",
    }:
        missing.append("decision")
    if missing:
        raise ValueError(f"WeKnora promotion task is missing required fields: {', '.join(missing)}")
    return normalized


def _normalize_human_resolution(
    resolution: dict[str, Any] | None, payload: dict[str, Any]
) -> dict[str, Any]:
    """Apply a human-approved write action to the parked candidate payload.

    The parked ``decision="human_review"`` row would immediately park again
    on re-execution (review round 2, R2-5); an approval therefore carries the
    human-determined action. The content is the COMPLETE post-operation body;
    the adapter's own contract validation still applies on execution.
    """
    if not isinstance(resolution, dict):
        raise ValueError("approve requires a resolution object")
    action = str(resolution.get("action") or "").strip()
    if action not in {"new", "supplement", "replace", "merge"}:
        raise ValueError("resolution.action must be one of new/supplement/replace/merge")
    content = str(resolution.get("content") or "").strip()
    if not content:
        raise ValueError("resolution.content is required (the complete post-operation body)")
    updated = dict(payload)
    updated["decision"] = action
    updated["content"] = content
    # Review round 3, R3-4: merge reads merged_content — the human-approved
    # body IS the complete post-merge body, so it must land there too (and
    # any stale, unapproved merged draft must not survive).
    if action == "merge":
        updated["merged_content"] = content
    for field in ("title", "target_object_id", "base_version", "kind", "merged_content"):
        value = str(resolution.get(field) or "").strip()
        if value:
            updated[field] = value
    if resolution.get("importance") is not None:
        try:
            updated["importance"] = int(resolution["importance"])
        except (TypeError, ValueError) as exc:
            raise ValueError("resolution.importance must be an integer") from exc
    return updated


class InMemoryWeKnoraPromotionRepositoryMixin:
    def _weknora_promotion_state(self) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_weknora_promotions"):
            self._weknora_promotions: dict[str, dict[str, Any]] = {}
        return self._weknora_promotions

    def _enqueue_weknora_promotions_locked(
        self, tasks: list[dict[str, Any]], *, now_value: str
    ) -> list[dict[str, Any]]:
        state = self._weknora_promotion_state()
        inserted: list[dict[str, Any]] = []
        for task in tasks:
            normalized = normalize_weknora_promotion_task(task, now_value=now_value)
            if normalized["promotion_id"] in state:
                continue
            state[normalized["promotion_id"]] = copy.deepcopy(normalized)
            inserted.append(copy.deepcopy(normalized))
        return inserted

    def enqueue_weknora_promotions(self, tasks: list[dict[str, Any]], *, now_value: str) -> list[dict[str, Any]]:
        with self._assignment_lock:
            return self._enqueue_weknora_promotions_locked(tasks, now_value=now_value)

    def list_weknora_promotions(self) -> list[dict[str, Any]]:
        with self._assignment_lock:
            return [
                copy.deepcopy(row)
                for row in self._weknora_promotion_state().values()
            ]

    def claim_weknora_promotion(
        self, promotion_id: str, *, owner_token: str, claimed_at: str, lease_expires_at: str
    ) -> dict[str, Any] | None:
        with self._assignment_lock:
            row = self._weknora_promotion_state().get(str(promotion_id))
            if row is None or row["status"] not in {"queued", "active"}:
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

    def complete_weknora_promotion(
        self,
        promotion_id: str,
        *,
        owner_token: str,
        status: str,
        weknora_object_id: str | None = None,
        weknora_version: str | None = None,
        receipt: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_detail: str | None = None,
        completed_at: str,
    ) -> dict[str, Any]:
        if status not in {"accepted", "failed", "outcome_unknown", "human_review"}:
            raise ValueError("invalid WeKnora promotion completion status")
        with self._assignment_lock:
            row = self._weknora_promotion_state().get(str(promotion_id))
            if row is None or row.get("owner_token") != owner_token:
                raise RuntimeError("stale WeKnora promotion delivery")
            if row["status"] != "active":
                # Late receipt (review round 1, P1-9): the adapter already
                # produced its external effect when reopen invalidated the
                # row. Record the evidence but keep the invalidated status —
                # the reopen decision is never resurrected by a late write.
                if row["status"] not in {"invalidated", "superseded"}:
                    raise RuntimeError("stale WeKnora promotion delivery")
                row.update(
                    weknora_object_id=weknora_object_id,
                    weknora_version=weknora_version,
                    operation_receipt=copy.deepcopy(receipt),
                    failure_code=failure_code,
                    failure_detail=failure_detail,
                    updated_at=completed_at,
                )
                return {**copy.deepcopy(row), "late_receipt_recorded": True}
            row.update(
                status=status,
                weknora_object_id=weknora_object_id,
                weknora_version=weknora_version,
                operation_receipt=copy.deepcopy(receipt),
                failure_code=failure_code,
                failure_detail=failure_detail,
                lease_expires_at=None,
                updated_at=completed_at,
            )
            return copy.deepcopy(row)

    def invalidate_weknora_promotions_for_case(
        self, engineer_case_id: str, *, invalidated_at: str
    ) -> int:
        with self._assignment_lock:
            invalidated = 0
            for row in self._weknora_promotion_state().values():
                # Same widened set as reopen (review round 1, P1-9): parked
                # terminal-pending states must not stay requeueable after the
                # case-level invalidation decision.
                if row["engineer_case_id"] == str(engineer_case_id) and row["status"] in {
                    "queued", "active", "human_review", "failed", "outcome_unknown",
                }:
                    row.update(status="invalidated", lease_expires_at=None, updated_at=invalidated_at)
                    invalidated += 1
            return invalidated

    def requeue_weknora_promotion(
        self, promotion_id: str, *, requeued_at: str, reason: str
    ) -> dict[str, Any] | None:
        with self._assignment_lock:
            row = self._weknora_promotion_state().get(str(promotion_id))
            if row is None or row["status"] not in {"failed", "outcome_unknown", "human_review"}:
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

    def decide_weknora_promotion(
        self,
        promotion_id: str,
        *,
        decision: str,
        decided_by: str,
        note: str = "",
        decided_at: str,
        resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Close the human-review loop (governance plan WP3, review round 1).

        ``approve`` applies the human-determined write action (review round 2,
        R2-5: a bare re-queue would park again on decision="human_review") and
        re-queues the promotion — the write then re-enters the full external
        contract (idempotency key, version protection), never a bypass.
        ``reject`` parks it terminally. Only rows actually sitting in
        ``human_review`` are decidable; any other state returns None so a
        stale decision cannot resurrect finished work.
        """
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        detail = str(decided_by or "").strip()
        if str(note or "").strip():
            detail = f"{detail}: {str(note).strip()}" if detail else str(note).strip()
        with self._assignment_lock:
            row = self._weknora_promotion_state().get(str(promotion_id))
            if row is None or row["status"] != "human_review":
                return None
            next_payload = row["candidate_payload"]
            next_decision = row["decision"]
            if decision == "approve":
                next_payload = _normalize_human_resolution(resolution, row["candidate_payload"])
                next_decision = next_payload["decision"]
                row["candidate_payload"] = copy.deepcopy(next_payload)
                row["decision"] = next_decision
                # Review round 3, R3-4: drop the parked attempt's recorded
                # WeKnora object/version — on re-execution the recovery
                # reconcile would compare the OLD stored body with the
                # human-approved body and park again (reconcile_content_
                # mismatch). The human has already seen and superseded the
                # parked state; the evidence stays in operation_receipt.
                row["weknora_object_id"] = None
                row["weknora_version"] = None
            row.update(
                status="queued" if decision == "approve" else "rejected",
                owner_token=None,
                claimed_at=None,
                lease_expires_at=None,
                human_decision="approved" if decision == "approve" else "rejected",
                human_decision_detail=detail or None,
                human_decided_at=decided_at,
                updated_at=decided_at,
            )
            return copy.deepcopy(row)


_WEKNORA_PROMOTION_TABLE = "support_weknora_promotions"


class PostgresWeKnoraPromotionRepositoryMixin:
    def _initialize_weknora_schema(self, cur: psycopg.Cursor[Any]) -> None:
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    promotion_id TEXT PRIMARY KEY,
                    -- Standalone (case-less) sources use NULL lineage; see the
                    -- v19 migration below for the FK/NOT NULL relaxation.
                    engineer_case_id TEXT REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                    client_ticket_id TEXT,
                    investigation_id TEXT,
                    summary_session_id TEXT, summary_run_id TEXT,
                    review_session_id TEXT, review_run_id TEXT,
                    slack_channel_id TEXT, slack_thread_ts TEXT,
                    source_type TEXT NOT NULL, source_id TEXT NOT NULL,
                    source_version TEXT NOT NULL, content_hash TEXT NOT NULL,
                    candidate_type TEXT NOT NULL CHECK (candidate_type IN ('knowledge','memory','skill')),
                    decision TEXT NOT NULL CHECK (decision IN (
                        'no_change','new','supplement','replace','merge','human_review'
                    )),
                    candidate_payload JSONB NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'queued','active','accepted','failed','outcome_unknown','human_review','invalidated','rejected'
                    )),
                    owner_token TEXT, claimed_at TIMESTAMPTZ, lease_expires_at TIMESTAMPTZ,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    weknora_object_id TEXT, weknora_version TEXT, operation_receipt JSONB,
                    failure_code TEXT, failure_detail TEXT,
                    human_decision TEXT, human_decision_detail TEXT, human_decided_at TIMESTAMPTZ,
                    input_fingerprint TEXT,
                    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
                )
                """
            ).format(
                self._table(_WEKNORA_PROMOTION_TABLE),
                self._table("support_engineer_cases"),
            )
        )
        # v15: uniqueness is per candidate (content hash), not per source
        # version, so multiple same-type candidates from one close survive
        # while replayed events still dedupe. The v14 index is replaced.
        cur.execute(
            sql.SQL("DROP INDEX IF EXISTS {}").format(
                sql.Identifier("idx_support_weknora_promotions_source_unique")
            )
        )
        cur.execute(
            sql.SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} "
                "(source_type, source_id, source_version, candidate_type, content_hash)"
            ).format(
                sql.Identifier("idx_support_weknora_promotions_candidate_unique"),
                self._table(_WEKNORA_PROMOTION_TABLE),
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, created_at, promotion_id)").format(
                sql.Identifier("idx_support_weknora_promotions_claim"),
                self._table(_WEKNORA_PROMOTION_TABLE),
            )
        )
        promotion_table = self._table(_WEKNORA_PROMOTION_TABLE)
        # v19: relax NOT NULL on the case lineage for standalone
        # (case-less) promotions (knowledge_source_review); existing v18
        # databases need the column constraint dropped.
        cur.execute(sql.SQL(
            "ALTER TABLE {} ALTER COLUMN engineer_case_id DROP NOT NULL"
        ).format(promotion_table))
        cur.execute(sql.SQL(
            "ALTER TABLE {} ALTER COLUMN client_ticket_id DROP NOT NULL"
        ).format(promotion_table))
        # v19: human-review decision closure (governance plan WP3, review
        # round 1) — the 'rejected' terminal status plus the decision
        # evidence columns. Existing v18 databases need both the CHECK
        # re-issue and the new columns.
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS human_decision TEXT").format(promotion_table))
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS human_decision_detail TEXT").format(promotion_table))
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS human_decided_at TIMESTAMPTZ").format(promotion_table))
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS input_fingerprint TEXT").format(promotion_table))
        cur.execute(
            sql.SQL("ALTER TABLE {} DROP CONSTRAINT IF EXISTS {}").format(
                promotion_table,
                sql.Identifier("support_weknora_promotions_status_check"),
            )
        )
        cur.execute(
            sql.SQL(
                "ALTER TABLE {} ADD CONSTRAINT {} CHECK (status IN ("
                "'queued','active','accepted','failed','outcome_unknown','human_review',"
                "'invalidated','rejected'))"
            ).format(
                promotion_table,
                sql.Identifier("support_weknora_promotions_status_check"),
            )
        )
        # v16: skill proposals enter the pipeline as human-review-only records;
        # swap the legacy (knowledge, memory) check on databases created before
        # the enum extension.
        cur.execute(
            sql.SQL("ALTER TABLE {} DROP CONSTRAINT IF EXISTS {}").format(
                promotion_table,
                sql.Identifier("support_weknora_promotions_candidate_type_check"),
            )
        )
        cur.execute(
            sql.SQL(
                "ALTER TABLE {} ADD CONSTRAINT {} CHECK "
                "(candidate_type IN ('knowledge','memory','skill'))"
            ).format(
                promotion_table,
                sql.Identifier("support_weknora_promotions_candidate_type_check"),
            )
        )

    def _enqueue_weknora_promotions_cur(
        self, cur: psycopg.Cursor[Any], tasks: list[dict[str, Any]], *, now_value: str
    ) -> list[dict[str, Any]]:
        inserted: list[dict[str, Any]] = []
        for task in tasks:
            normalized = normalize_weknora_promotion_task(task, now_value=now_value)
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {} (promotion_id, engineer_case_id, client_ticket_id, investigation_id,
                        summary_session_id, summary_run_id, review_session_id, review_run_id,
                        slack_channel_id, slack_thread_ts, source_type, source_id, source_version,
                        content_hash, candidate_type, decision, candidate_payload, status,
                        attempt_count, input_fingerprint, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',0,%s,%s,%s)
                    ON CONFLICT (promotion_id) DO NOTHING
                    RETURNING promotion_id
                    """
                ).format(self._table(_WEKNORA_PROMOTION_TABLE)),
                (
                    normalized["promotion_id"],
                    # Standalone (case-less) promotions store NULL lineage in
                    # PG — the FK would reject the empty string.
                    normalized["engineer_case_id"] or None,
                    normalized["client_ticket_id"] or None,
                    normalized["investigation_id"],
                    normalized["summary_session_id"],
                    normalized["summary_run_id"],
                    normalized["review_session_id"],
                    normalized["review_run_id"],
                    normalized["slack_channel_id"],
                    normalized["slack_thread_ts"],
                    normalized["source_type"],
                    normalized["source_id"],
                    normalized["source_version"],
                    normalized["content_hash"],
                    normalized["candidate_type"],
                    normalized["decision"],
                    Json(normalized["candidate_payload"]),
                    normalized["input_fingerprint"] or None,
                    now_value,
                    now_value,
                ),
            )
            if cur.fetchone() is not None:
                inserted.append(normalized)
        return inserted

    def enqueue_weknora_promotions(self, tasks: list[dict[str, Any]], *, now_value: str) -> list[dict[str, Any]]:
        def operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.transaction(), conn.cursor() as cur:
                return self._enqueue_weknora_promotions_cur(cur, tasks, now_value=now_value)

        return self._run_with_connection_retry("enqueue_weknora_promotions", operation)

    def list_weknora_promotions(self) -> list[dict[str, Any]]:
        def operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT {} FROM {} ORDER BY created_at, promotion_id"
                    ).format(
                        sql.SQL(",").join(map(sql.Identifier, WEKNORA_PROMOTION_FIELDS)),
                        self._table(_WEKNORA_PROMOTION_TABLE),
                    )
                )
                rows = []
                for record in cur.fetchall():
                    row = dict(zip(WEKNORA_PROMOTION_FIELDS, record))
                    row["candidate_payload"] = _json_value(row.get("candidate_payload"))
                    row["operation_receipt"] = _json_value(row.get("operation_receipt"))
                    row["created_at"] = _iso(row.get("created_at"))
                    row["updated_at"] = _iso(row.get("updated_at"))
                    row["claimed_at"] = _iso(row.get("claimed_at"))
                    row["lease_expires_at"] = _iso(row.get("lease_expires_at"))
                    rows.append(row)
                return rows

        return self._run_with_connection_retry("list_weknora_promotions", operation)

    def claim_weknora_promotion(
        self, promotion_id: str, *, owner_token: str, claimed_at: str, lease_expires_at: str
    ) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status='active', owner_token=%s, claimed_at=%s,
                        lease_expires_at=%s, attempt_count=attempt_count+1, updated_at=%s
                        WHERE promotion_id=%s
                        AND (status='queued' OR (status='active' AND lease_expires_at<=%s))
                        RETURNING {}
                        """
                    ).format(
                        self._table(_WEKNORA_PROMOTION_TABLE),
                        sql.SQL(",").join(map(sql.Identifier, WEKNORA_PROMOTION_FIELDS)),
                    ),
                    (owner_token, claimed_at, lease_expires_at, claimed_at, promotion_id, claimed_at),
                )
                record = cur.fetchone()
                if record is None:
                    return None
                row = dict(zip(WEKNORA_PROMOTION_FIELDS, record))
                row["candidate_payload"] = _json_value(row.get("candidate_payload"))
                row["operation_receipt"] = _json_value(row.get("operation_receipt"))
                row["created_at"] = _iso(row.get("created_at"))
                row["updated_at"] = _iso(row.get("updated_at"))
                row["claimed_at"] = _iso(row.get("claimed_at"))
                row["lease_expires_at"] = _iso(row.get("lease_expires_at"))
                return row

        return self._run_with_connection_retry("claim_weknora_promotion", operation)

    def complete_weknora_promotion(
        self,
        promotion_id: str,
        *,
        owner_token: str,
        status: str,
        weknora_object_id: str | None = None,
        weknora_version: str | None = None,
        receipt: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_detail: str | None = None,
        completed_at: str,
    ) -> dict[str, Any]:
        if status not in {"accepted", "failed", "outcome_unknown", "human_review"}:
            raise ValueError("invalid WeKnora promotion completion status")

        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status=%s, weknora_object_id=%s, weknora_version=%s,
                        operation_receipt=%s, failure_code=%s, failure_detail=%s,
                        lease_expires_at=NULL, updated_at=%s
                        WHERE promotion_id=%s AND status='active' AND owner_token=%s
                        RETURNING promotion_id
                        """
                    ).format(self._table(_WEKNORA_PROMOTION_TABLE)),
                    (
                        status,
                        weknora_object_id,
                        weknora_version,
                        Json(receipt) if receipt is not None else None,
                        failure_code,
                        failure_detail,
                        completed_at,
                        promotion_id,
                        owner_token,
                    ),
                )
                if cur.fetchone() is None:
                    # Late receipt (review round 1, P1-9): the adapter's
                    # external effect already happened when reopen moved the
                    # row out of 'active'. Persist the evidence, keep the
                    # invalidated status, and report the marker — the reopen
                    # decision is never resurrected by a late write.
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {} SET weknora_object_id=%s, weknora_version=%s,
                            operation_receipt=%s, failure_code=%s, failure_detail=%s,
                            updated_at=%s
                            WHERE promotion_id=%s AND owner_token=%s
                            AND status IN ('invalidated','superseded')
                            RETURNING status
                            """
                        ).format(self._table(_WEKNORA_PROMOTION_TABLE)),
                        (
                            weknora_object_id,
                            weknora_version,
                            Json(receipt) if receipt is not None else None,
                            failure_code,
                            failure_detail,
                            completed_at,
                            promotion_id,
                            owner_token,
                        ),
                    )
                    record = cur.fetchone()
                    if record is None:
                        raise RuntimeError("stale WeKnora promotion delivery")
                    return {
                        "promotion_id": promotion_id,
                        "status": str(record[0]),
                        "weknora_object_id": weknora_object_id,
                        "weknora_version": weknora_version,
                        "operation_receipt": receipt,
                        "failure_code": failure_code,
                        "failure_detail": failure_detail,
                        "late_receipt_recorded": True,
                    }
                return {
                    "promotion_id": promotion_id,
                    "status": status,
                    "weknora_object_id": weknora_object_id,
                    "weknora_version": weknora_version,
                    "operation_receipt": receipt,
                    "failure_code": failure_code,
                    "failure_detail": failure_detail,
                }

        return self._run_with_connection_retry("complete_weknora_promotion", operation)

    def invalidate_weknora_promotions_for_case(
        self, engineer_case_id: str, *, invalidated_at: str
    ) -> int:
        def operation(conn: psycopg.Connection[Any]) -> int:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status='invalidated', lease_expires_at=NULL, updated_at=%s
                        WHERE engineer_case_id=%s
                        AND status IN ('queued','active','human_review','failed','outcome_unknown')
                        """
                    ).format(self._table(_WEKNORA_PROMOTION_TABLE)),
                    (invalidated_at, engineer_case_id),
                )
                return cur.rowcount

        return self._run_with_connection_retry("invalidate_weknora_promotions_for_case", operation)

    def requeue_weknora_promotion(
        self, promotion_id: str, *, requeued_at: str, reason: str
    ) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status='queued', owner_token=NULL, claimed_at=NULL,
                        lease_expires_at=NULL, failure_detail=%s, updated_at=%s
                        WHERE promotion_id=%s AND status IN ('failed','outcome_unknown','human_review')
                        RETURNING promotion_id
                        """
                    ).format(self._table(_WEKNORA_PROMOTION_TABLE)),
                    (f"requeued: {reason}", requeued_at, promotion_id),
                )
                record = cur.fetchone()
                return None if record is None else {"promotion_id": promotion_id, "status": "queued"}

        return self._run_with_connection_retry("requeue_weknora_promotion", operation)

    def decide_weknora_promotion(
        self,
        promotion_id: str,
        *,
        decision: str,
        decided_by: str,
        note: str = "",
        decided_at: str,
        resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """PG twin of the human-review decision closure; see the in-memory
        method for the contract (approve applies the human-determined write
        action and re-queues under the full external write contract, reject is
        terminal, only human_review is decidable)."""
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        detail = str(decided_by or "").strip()
        if str(note or "").strip():
            detail = f"{detail}: {str(note).strip()}" if detail else str(note).strip()
        next_status = "queued" if decision == "approve" else "rejected"

        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT candidate_payload, decision, weknora_object_id, weknora_version "
                        "FROM {} WHERE promotion_id=%s AND status='human_review' FOR UPDATE"
                    ).format(self._table(_WEKNORA_PROMOTION_TABLE)),
                    (promotion_id,),
                )
                record = cur.fetchone()
                if record is None:
                    return None
                stored_payload = _json_value(record[0]) or {}
                next_decision = str(record[1] or "")
                next_payload = stored_payload
                clear_object = False
                if decision == "approve":
                    next_payload = _normalize_human_resolution(resolution, stored_payload)
                    next_decision = next_payload["decision"]
                    clear_object = True
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status=%s, owner_token=NULL, claimed_at=NULL,
                        lease_expires_at=NULL, human_decision=%s, human_decision_detail=%s,
                        human_decided_at=%s, updated_at=%s, decision=%s, candidate_payload=%s,
                        weknora_object_id=%s, weknora_version=%s
                        WHERE promotion_id=%s AND status='human_review'
                        RETURNING promotion_id
                        """
                    ).format(self._table(_WEKNORA_PROMOTION_TABLE)),
                    (
                        next_status,
                        "approved" if decision == "approve" else "rejected",
                        detail or None,
                        decided_at,
                        decided_at,
                        next_decision,
                        Json(next_payload),
                        # Review round 3, R3-4: clear the parked attempt's
                        # recorded object/version so the approved write is not
                        # re-parked by the recovery reconcile.
                        None if clear_object else record[2] if len(record) > 2 else None,
                        None if clear_object else record[3] if len(record) > 3 else None,
                        promotion_id,
                    ),
                )
                if cur.fetchone() is None:
                    return None
                return {
                    "promotion_id": promotion_id,
                    "status": next_status,
                    "decision": next_decision,
                }

        return self._run_with_connection_retry("decide_weknora_promotion", operation)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


__all__ = [
    "InMemoryWeKnoraPromotionRepositoryMixin",
    "PostgresWeKnoraPromotionRepositoryMixin",
    "normalize_weknora_promotion_task",
    "weknora_promotion_id",
    "WEKNORA_PROMOTION_STATUSES",
]

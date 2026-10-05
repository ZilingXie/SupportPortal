"""Map a reviewed WeKnora promotion candidate to controlled WeKnora operations.

The Adapter never decides whether content is knowledge, memory, or a skill:
classification and decision come from the Review output.  The Adapter only
validates the candidate and executes it with the failure model agreed in the
WeKnora adapter plan:

- incomplete review output      -> failed (no write)
- search/read failure           -> no write, outcome_unknown or human review
- target version changed        -> no overwrite, human review
- targeted decision without the review's base version -> failed (no write)
- update op without probe-proven conditional-update support -> human review
- 401/403                       -> failed (no retry)
- network timeout on write      -> outcome_unknown (same idempotency key on retry)
- write ok but readback cannot prove object/content/version -> outcome_unknown
- retried task with a known object -> readback reconciliation first, never a
  blind second write
- memory without shared identity-> human review (no global memory write)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.services.weknora_client import WeKnoraClient, WeKnoraError

WRITE_DECISIONS = frozenset({"new", "supplement", "replace", "merge"})
TARGETED_DECISIONS = frozenset({"supplement", "replace", "merge"})

# The pinned lineage DTO (review round 1 contract gap): the exact task fields
# that travel as WeKnora write metadata, so a stored object traces back to the
# case, ticket, summary/review runs, and Slack thread that produced it. The
# client's pinned body template decides whether the destination API actually
# receives them; adding a field here is a contract change.
WEKNORA_LINEAGE_METADATA_FIELDS = (
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
)


@dataclass(frozen=True)
class WeKnoraPromotionOutcome:
    status: str  # accepted | failed | outcome_unknown | human_review
    failure_code: str | None = None
    failure_detail: str | None = None
    weknora_object_id: str | None = None
    weknora_version: str | None = None
    receipt: dict[str, Any] | None = None


def _item_object_id(item: dict[str, Any]) -> str:
    """WeKnora APIs may answer with ``id`` instead of ``object_id``."""
    return str(item.get("object_id") or item.get("id") or "").strip()


class WeKnoraPromotionAdapter:
    def __init__(self, client: WeKnoraClient) -> None:
        self._client = client

    def execute(self, task: dict[str, Any]) -> WeKnoraPromotionOutcome:
        candidate = task.get("candidate_payload") if isinstance(task.get("candidate_payload"), dict) else {}
        decision = str(task.get("decision") or "").strip()
        candidate_type = str(task.get("candidate_type") or "").strip()
        # Review round 4, R4-4: a HUMAN-APPROVED revision is a NEW operation,
        # not a retry of the original one. The original key may already hold
        # a successful receipt (a write whose readback timed out), which
        # would 409 the different approved request forever. The approved
        # revision therefore carries its own retryable identity, keyed by the
        # decision timestamp: retries of THIS approval replay it, and the
        # original receipt stays untouched for audit.
        idempotency_key = str(task.get("promotion_id") or "").strip()
        decided_at = str(task.get("human_decided_at") or "").strip()
        if str(task.get("human_decision") or "") == "approved" and decided_at:
            idempotency_key = f"{idempotency_key}:h{decided_at}"

        if decision == "no_change":
            return WeKnoraPromotionOutcome(
                status="accepted",
                receipt={"operation": "no_change", "decision": decision},
            )
        if decision == "human_review":
            return WeKnoraPromotionOutcome(
                status="human_review", failure_code="review_requested_human_review"
            )
        if candidate_type == "skill":
            # Skills are human-maintained: even a malformed write-intent skill
            # row becomes an explicit human-review record instead of a write
            # or a silent drop.
            return WeKnoraPromotionOutcome(
                status="human_review",
                failure_code="skill_change_requires_human_review",
                failure_detail=(
                    "skill candidates are proposals for human maintainers; "
                    "the adapter never writes skills"
                ),
            )
        if decision not in WRITE_DECISIONS or candidate_type not in {"knowledge", "memory"}:
            return WeKnoraPromotionOutcome(
                status="failed", failure_code="invalid_candidate",
                failure_detail="unsupported candidate_type/decision",
            )
        if not idempotency_key:
            return WeKnoraPromotionOutcome(
                status="failed", failure_code="invalid_candidate", failure_detail="missing promotion_id"
            )

        content = self._write_content(candidate, decision)
        title = str(candidate.get("title") or "").strip()
        if not content:
            return WeKnoraPromotionOutcome(
                status="failed", failure_code="invalid_candidate",
                failure_detail="incomplete review output: content is required",
            )
        if decision == "new" and candidate_type == "knowledge" and not title:
            return WeKnoraPromotionOutcome(
                status="failed", failure_code="invalid_candidate",
                failure_detail="incomplete review output: new knowledge requires a title",
            )
        base_version = str(candidate.get("base_version") or "").strip()
        if decision in TARGETED_DECISIONS and not base_version:
            return WeKnoraPromotionOutcome(
                status="failed", failure_code="invalid_candidate",
                failure_detail=(
                    f"{decision} requires the base_version the review based its content on; "
                    "refusing to update against an unpinned current version"
                ),
            )

        if candidate_type == "memory" and not self._client.has_memory_identity():
            return WeKnoraPromotionOutcome(
                status="human_review", failure_code="memory_identity_not_configured",
                failure_detail="shared Hermes memory identity is not pinned; refusing to write global memory",
            )

        target_object_id = str(candidate.get("target_object_id") or "").strip()

        # Recovery precedes validation (review round 1, P1-9): a retried task
        # that already recorded a WeKnora object must reconcile against that
        # recorded write BEFORE the base_version check — its own earlier
        # execution legitimately advanced the target past the review's
        # base_version, and comparing first would misreport the completed
        # write as target_version_conflict.
        known_object_id = str(task.get("weknora_object_id") or "").strip()
        if known_object_id:
            reconciliation = self._reconcile_known_object(
                candidate_type=candidate_type,
                decision=decision,
                known_object_id=known_object_id,
                known_version=str(task.get("weknora_version") or "").strip(),
                expected_content=content,
                target_object_id=target_object_id,
            )
            if reconciliation is not None:
                return reconciliation

        if decision in TARGETED_DECISIONS:
            if not target_object_id:
                return WeKnoraPromotionOutcome(
                    status="failed", failure_code="invalid_candidate",
                    failure_detail=f"{decision} requires target_object_id",
                )
            # A version-protected update requires probe-proven conditional
            # update support; without that evidence the task goes to humans
            # instead of an unverifiable overwrite.
            if not self._client.supports_conditional_update(candidate_type):
                return WeKnoraPromotionOutcome(
                    status="human_review", failure_code="conditional_update_unsupported",
                    failure_detail=(
                        "the pinned WeKnora contract does not declare "
                        f"{candidate_type}_update conditional_update=true; refusing "
                        "version-protected targeted writes without probe evidence"
                    ),
                )
            current = self._read_target(candidate_type, target_object_id)
            if isinstance(current, WeKnoraPromotionOutcome):
                return current
            current_version = str(current.get("version") or "").strip()
            if not current_version:
                return WeKnoraPromotionOutcome(
                    status="human_review", failure_code="target_version_unknown",
                    failure_detail="target current version is not readable; refusing to overwrite",
                    weknora_object_id=target_object_id,
                )
            # The review must pin the version it based its decision on; a
            # changed target is never silently overwritten with current.
            if base_version != current_version:
                return WeKnoraPromotionOutcome(
                    status="human_review", failure_code="target_version_conflict",
                    failure_detail=f"target is at version {current_version or 'unknown'}, review based on {base_version}",
                    weknora_object_id=target_object_id,
                    weknora_version=current_version or None,
                )
            # proposed_content is the COMPLETE post-operation body (review
            # manual v2): the adapter submits it verbatim. Never prepend the
            # stored body — that double-concatenates when the review already
            # integrated the existing text (review round 1, P1-8).
            resolved_content = content
            resolved_title = title or str(current.get("title") or "")
            resolved_base_version = current_version
        else:
            resolved_content = content
            resolved_title = title
            resolved_base_version = ""

        return self._write_and_readback(
            candidate_type=candidate_type,
            decision=decision,
            title=resolved_title,
            content=resolved_content,
            target_object_id=target_object_id or None,
            base_version=resolved_base_version,
            idempotency_key=idempotency_key,
            kind=str(candidate.get("kind") or "").strip(),
            importance=candidate.get("importance"),
            metadata=self._lineage_metadata(task),
        )

    # -- internals ----------------------------------------------------------

    def _lineage_metadata(self, task: dict[str, Any]) -> dict[str, Any]:
        """Full SupportPortal lineage travels with every WeKnora write so the
        stored object traces back to the case, ticket, summary/review runs,
        and Slack thread that produced it. The client's pinned body template
        decides whether the destination API actually receives it."""
        metadata = {
            key: str(task.get(key) or "").strip()
            for key in WEKNORA_LINEAGE_METADATA_FIELDS
            if str(task.get(key) or "").strip()
        }
        metadata["promotion_id"] = str(task.get("promotion_id") or "")
        metadata["candidate_type"] = str(task.get("candidate_type") or "")
        metadata["decision"] = str(task.get("decision") or "")
        return metadata

    def _write_content(self, candidate: dict[str, Any], decision: str) -> str:
        if decision == "merge":
            return str(candidate.get("merged_content") or "").strip()
        return str(candidate.get("content") or "").strip()

    def _read_object(self, candidate_type: str, object_id: str) -> dict[str, Any]:
        """Read one object; raises WeKnoraError (not_found when absent).

        Memory uses the official list endpoint: the walk covers EVERY page so
        an object on a later page is found (zero extra creates). A read
        failure on any page propagates as its own failure kind — an
        incomplete read is never treated as absence.
        """
        if candidate_type == "knowledge":
            return self._client.knowledge_read(object_id=object_id)
        list_all = getattr(self._client, "memory_list_all", None)
        items = (
            list_all() if callable(list_all) else list(self._client.memory_list())
        )
        for item in items:
            if _item_object_id(item) == object_id:
                return {
                    "object_id": object_id,
                    "version": str(item.get("version") or ""),
                    "title": str(item.get("title") or ""),
                    "content": str(item.get("content") or ""),
                }
        raise WeKnoraError("memory target not found", failure_kind="not_found")

    def _read_target(self, candidate_type: str, target_object_id: str) -> dict[str, Any] | WeKnoraPromotionOutcome:
        try:
            return self._read_object(candidate_type, target_object_id)
        except WeKnoraError as exc:
            if exc.failure_kind == "not_found":
                return WeKnoraPromotionOutcome(
                    status="human_review", failure_code="target_not_found",
                    failure_detail=str(exc), weknora_object_id=target_object_id,
                )
            if exc.failure_kind == "auth":
                return WeKnoraPromotionOutcome(
                    status="failed", failure_code="weknora_auth_rejected", failure_detail=str(exc)
                )
            if exc.failure_kind in {"timeout", "transport", "http", "invalid_response"}:
                return WeKnoraPromotionOutcome(
                    status="outcome_unknown", failure_code=f"weknora_read_{exc.failure_kind}",
                    failure_detail=str(exc),
                )
            return WeKnoraPromotionOutcome(
                status="failed", failure_code=f"weknora_read_{exc.failure_kind}", failure_detail=str(exc)
            )

    def _reconcile_known_object(
        self,
        *,
        candidate_type: str,
        decision: str,
        known_object_id: str,
        known_version: str,
        expected_content: str,
        target_object_id: str,
    ) -> WeKnoraPromotionOutcome | None:
        """A retried task that already recorded a WeKnora object must prove the
        earlier write state by readback before any new external call.

        Returns None when reconciliation proves the earlier write never landed
        (object absent), letting the normal write path proceed with the same
        idempotency key.
        """
        try:
            read = self._read_object(candidate_type, known_object_id)
        except WeKnoraError as exc:
            if exc.failure_kind == "not_found":
                return None  # prior write never landed; safe to write now
            if exc.failure_kind == "auth":
                return WeKnoraPromotionOutcome(
                    status="failed", failure_code="weknora_auth_rejected", failure_detail=str(exc),
                    weknora_object_id=known_object_id,
                )
            if exc.failure_kind in {"timeout", "transport", "http", "invalid_response"}:
                return WeKnoraPromotionOutcome(
                    status="outcome_unknown", failure_code=f"reconcile_read_{exc.failure_kind}",
                    failure_detail=str(exc), weknora_object_id=known_object_id,
                )
            return WeKnoraPromotionOutcome(
                status="failed", failure_code=f"reconcile_read_{exc.failure_kind}", failure_detail=str(exc),
                weknora_object_id=known_object_id,
            )

        read_content = str(read.get("content") or "").strip()
        # Full-body comparison for every decision: proposed_content carries
        # the complete post-operation body, so the readback must equal it.
        content_matches = read_content == expected_content
        read_version = str(read.get("version") or "").strip()
        version_matches = not known_version or not read_version or known_version == read_version
        if content_matches and version_matches:
            return WeKnoraPromotionOutcome(
                status="accepted",
                weknora_object_id=known_object_id,
                weknora_version=read_version or known_version or None,
                receipt={
                    "operation": "reconciled_existing",
                    "object_id": known_object_id,
                    "version": read_version or known_version or None,
                },
            )
        return WeKnoraPromotionOutcome(
            status="human_review", failure_code="reconcile_content_mismatch",
            failure_detail=(
                f"object {known_object_id} exists with different content/version than this "
                "task recorded; refusing to overwrite without human decision"
            ),
            weknora_object_id=known_object_id,
            weknora_version=read_version or None,
        )

    def _write_and_readback(
        self,
        *,
        candidate_type: str,
        decision: str,
        title: str,
        content: str,
        target_object_id: str | None,
        base_version: str,
        idempotency_key: str,
        kind: str = "",
        importance: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WeKnoraPromotionOutcome:
        try:
            if candidate_type == "knowledge":
                if decision == "new":
                    write = self._client.knowledge_create(
                        title=title, content=content, idempotency_key=idempotency_key,
                        metadata=metadata,
                    )
                else:
                    write = self._client.knowledge_update(
                        object_id=str(target_object_id or ""),
                        base_version=base_version,
                        title=title,
                        content=content,
                        idempotency_key=idempotency_key,
                        metadata=metadata,
                    )
            else:
                if decision == "new":
                    write = self._client.memory_create(
                        content=content, idempotency_key=idempotency_key,
                        kind=kind, importance=importance, metadata=metadata,
                    )
                else:
                    write = self._client.memory_update(
                        object_id=str(target_object_id or ""),
                        base_version=base_version,
                        content=content,
                        idempotency_key=idempotency_key,
                        kind=kind, importance=importance, metadata=metadata,
                    )
        except WeKnoraError as exc:
            if exc.failure_kind == "auth":
                return WeKnoraPromotionOutcome(
                    status="failed", failure_code="weknora_auth_rejected", failure_detail=str(exc)
                )
            if exc.failure_kind == "conflict":
                return WeKnoraPromotionOutcome(
                    status="human_review", failure_code="target_version_conflict",
                    failure_detail=str(exc),
                )
            if exc.failure_kind in {"timeout", "transport"} or (
                exc.failure_kind == "http" and exc.status_code is not None and exc.status_code >= 500
            ):
                return WeKnoraPromotionOutcome(
                    status="outcome_unknown", failure_code=f"weknora_write_{exc.failure_kind}",
                    failure_detail=str(exc),
                )
            if exc.failure_kind == "invalid_response":
                # The write may have landed but the receipt could not be parsed.
                return WeKnoraPromotionOutcome(
                    status="outcome_unknown", failure_code="weknora_write_invalid_receipt",
                    failure_detail=str(exc),
                )
            return WeKnoraPromotionOutcome(
                status="failed", failure_code=f"weknora_write_{exc.failure_kind}", failure_detail=str(exc)
            )

        object_id = str(write.get("object_id") or "").strip()
        version = str(write.get("version") or "").strip() or None
        readback_error: str | None = None
        try:
            read = self._read_object(candidate_type, object_id)
        except WeKnoraError as exc:
            read = None
            readback_error = f"{exc.failure_kind}: {exc}"

        if readback_error is None:
            read_object_id = _item_object_id(read)
            read_content = str(read.get("content") or "").strip()
            read_version = str(read.get("version") or "").strip()
            # Readback must prove the write: same object, same content, and a
            # consistent version. Anything else is an unproven write.
            if read_object_id != object_id:
                readback_error = f"object mismatch: wrote {object_id}, read back {read_object_id or 'none'}"
            elif read_content != content:
                readback_error = "content mismatch: readback does not match the written content"
            elif version and read_version and version != read_version:
                readback_error = f"version mismatch: receipt {version}, readback {read_version}"

        if readback_error is not None:
            return WeKnoraPromotionOutcome(
                status="outcome_unknown", failure_code="readback_failed",
                failure_detail=readback_error,
                weknora_object_id=object_id or None,
                weknora_version=version,
                receipt=write.get("receipt") if isinstance(write.get("receipt"), dict) else None,
            )
        return WeKnoraPromotionOutcome(
            status="accepted",
            weknora_object_id=object_id,
            weknora_version=version,
            receipt=write.get("receipt") if isinstance(write.get("receipt"), dict) else None,
        )

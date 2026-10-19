from __future__ import annotations

from typing import Any

import pytest

from backend.services.weknora_client import WeKnoraError
from backend.services.weknora_promotion_adapter import (
    WeKnoraPromotionAdapter,
    weknora_wait_until_ready,
)


class FakeWeKnoraStore:
    """In-memory WeKnora double with injectable per-call failures.

    Writes land in the store and reads serve the stored state, so readback
    verification is exercised for real: a stale read or a skewed version is
    produced by tampering with the served state, not by stubbing the check away.
    """

    def __init__(
        self,
        *,
        memory_identity: str = "hermes-service",
        read_error: WeKnoraError | None = None,
        write_error: WeKnoraError | None = None,
        readback_error: WeKnoraError | None = None,
        created_objects: dict[str, dict[str, Any]] | None = None,
        conditional_update: bool = True,
    ) -> None:
        self.memory_identity_value = memory_identity
        self.read_error = read_error
        self.write_error = write_error
        self.readback_error = readback_error
        self.conditional_update = conditional_update
        self.objects: dict[str, dict[str, Any]] = dict(created_objects or {})
        self.next_version = 100
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def has_memory_identity(self) -> bool:
        return bool(self.memory_identity_value)

    def supports_conditional_update(self, candidate_type: str) -> bool:
        return self.conditional_update

    # -- knowledge ----------------------------------------------------------

    def knowledge_read(self, *, object_id: str) -> dict[str, Any]:
        self.calls.append(("knowledge_read", {"object_id": object_id}))
        if self.read_error or self.readback_error:
            raise self.read_error or self.readback_error
        record = self.objects.get(object_id)
        if record is None:
            raise WeKnoraError("not found", failure_kind="not_found")
        return {
            "object_id": object_id,
            "version": record["version"],
            "title": record["title"],
            "content": record["content"],
        }

    def knowledge_create(self, *, title: str, content: str, idempotency_key: str, **kwargs) -> dict[str, Any]:
        self.calls.append(("knowledge_create", {"title": title, "content": content, "key": idempotency_key}))
        if self.write_error:
            raise self.write_error
        for object_id, record in self.objects.items():
            if record.get("idempotency_key") == idempotency_key:
                # server-side dedupe: same key returns the same object
                return {"object_id": object_id, "version": record["version"], "receipt": {"deduped": True}}
        object_id = f"doc-{len(self.objects) + 1}"
        self.next_version += 1
        self.objects[object_id] = {
            "title": title,
            "content": content,
            "version": str(self.next_version),
            "idempotency_key": idempotency_key,
        }
        return {"object_id": object_id, "version": str(self.next_version), "receipt": {"ok": True}}

    def knowledge_update(self, *, object_id: str, base_version: str, title: str, content: str, idempotency_key: str, **kwargs) -> dict[str, Any]:
        self.calls.append(
            ("knowledge_update", {"object_id": object_id, "base_version": base_version, "content": content, "key": idempotency_key})
        )
        if self.write_error:
            raise self.write_error
        record = self.objects.get(object_id)
        if record is None:
            raise WeKnoraError("not found", failure_kind="not_found")
        if base_version and record["version"] != base_version:
            raise WeKnoraError("version conflict", failure_kind="conflict")
        self.next_version += 1
        record.update(title=title, content=content, version=str(self.next_version))
        return {"object_id": object_id, "version": str(self.next_version), "receipt": {"ok": True}}

    # -- memory --------------------------------------------------------------

    def memory_list(self, *, top_k: int | None = None) -> list[dict[str, Any]]:
        self.calls.append(("memory_list", {}))
        if self.read_error or self.readback_error:
            raise self.read_error or self.readback_error
        return [
            {"object_id": object_id, "version": record["version"], "title": record["title"], "content": record["content"]}
            for object_id, record in self.objects.items()
            if object_id.startswith("mem-")
        ]

    def memory_create(self, *, content: str, idempotency_key: str, **kwargs) -> dict[str, Any]:
        self.calls.append(("memory_create", {"content": content, "key": idempotency_key}))
        if self.write_error:
            raise self.write_error
        object_id = f"mem-{len(self.objects) + 1}"
        self.next_version += 1
        self.objects[object_id] = {"title": "", "content": content, "version": str(self.next_version), "idempotency_key": idempotency_key}
        return {"object_id": object_id, "version": str(self.next_version), "receipt": {"ok": True}}

    def memory_update(self, *, object_id: str, base_version: str, content: str, idempotency_key: str, **kwargs) -> dict[str, Any]:
        self.calls.append(("memory_update", {"object_id": object_id, "base_version": base_version, "content": content, "key": idempotency_key}))
        if self.write_error:
            raise self.write_error
        record = self.objects.get(object_id)
        if record is None:
            raise WeKnoraError("not found", failure_kind="not_found")
        if base_version and record["version"] != base_version:
            raise WeKnoraError("version conflict", failure_kind="conflict")
        self.next_version += 1
        record.update(content=content, version=str(self.next_version))
        return {"object_id": object_id, "version": str(self.next_version), "receipt": {"ok": True}}


def _task(
    *,
    candidate_type: str = "knowledge",
    decision: str = "new",
    payload: dict[str, Any] | None = None,
    promotion_id: str = "weknora:hermes_case_promotion:123-1:1:knowledge:0f0e0d",
    weknora_object_id: str | None = None,
    weknora_version: str | None = None,
) -> dict[str, Any]:
    return {
        "promotion_id": promotion_id,
        "candidate_type": candidate_type,
        "decision": decision,
        "weknora_object_id": weknora_object_id,
        "weknora_version": weknora_version,
        "candidate_payload": payload
        or {
            "schema_version": "v1",
            "candidate_type": candidate_type,
            "decision": decision,
            "title": "Title",
            "content": "Body",
        },
    }


def test_no_change_records_decision_without_external_calls() -> None:
    client = FakeWeKnoraStore()
    outcome = WeKnoraPromotionAdapter(client).execute(_task(decision="no_change"))
    assert outcome.status == "accepted"
    assert outcome.receipt == {"operation": "no_change", "decision": "no_change"}
    assert client.calls == []


def test_human_review_decision_does_not_write() -> None:
    client = FakeWeKnoraStore()
    outcome = WeKnoraPromotionAdapter(client).execute(_task(decision="human_review"))
    assert outcome.status == "human_review"
    assert client.calls == []


def test_incomplete_review_output_fails_without_write() -> None:
    client = FakeWeKnoraStore()
    outcome = WeKnoraPromotionAdapter(client).execute(_task(payload={"title": "", "content": ""}))
    assert outcome.status == "failed"
    assert outcome.failure_code == "invalid_candidate"
    assert client.calls == []


def test_new_knowledge_write_and_readback_accepted() -> None:
    client = FakeWeKnoraStore()
    outcome = WeKnoraPromotionAdapter(client).execute(_task())
    assert outcome.status == "accepted"
    assert outcome.weknora_object_id == "doc-1"
    assert outcome.weknora_version == "101"
    assert [name for name, _ in client.calls] == ["knowledge_create", "knowledge_read"]


def test_memory_candidate_without_shared_identity_goes_to_human_review() -> None:
    client = FakeWeKnoraStore(memory_identity="")
    outcome = WeKnoraPromotionAdapter(client).execute(_task(candidate_type="memory"))
    assert outcome.status == "human_review"
    assert outcome.failure_code == "memory_identity_not_configured"
    assert client.calls == []


# -- review-acceptance defect 4: version protection -------------------------


def test_replace_without_base_version_is_rejected() -> None:
    client = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "existing", "version": "5"}}
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="replace",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "replace",
                "title": "T",
                "content": "New body",
                "target_object_id": "doc-7",
            },
        )
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "invalid_candidate"
    assert client.calls == []


def test_merge_without_base_version_is_rejected() -> None:
    client = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "existing", "version": "5"}}
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="merge",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "merge",
                "title": "T",
                "content": "",
                "merged_content": "Merged full body",
                "target_object_id": "doc-7",
            },
        )
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "invalid_candidate"
    assert client.calls == []


def test_supplement_honors_provided_base_version() -> None:
    client = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "existing", "version": "6"}}
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="supplement",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "supplement",
                "title": "",
                "content": "Additional finding",
                "target_object_id": "doc-7",
                "base_version": "5",
            },
        )
    )
    assert outcome.status == "human_review"
    assert outcome.failure_code == "target_version_conflict"
    assert client.calls == [("knowledge_read", {"object_id": "doc-7"})]


def test_supplement_without_base_version_is_rejected() -> None:
    """Review-acceptance round 2, blocker 1: supplement cannot adopt the
    current version as an unconfirmed review baseline."""
    client = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "existing", "version": "9"}}
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="supplement",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "supplement",
                "title": "",
                "content": "Additional finding",
                "target_object_id": "doc-7",
            },
        )
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "invalid_candidate"
    assert "base_version" in str(outcome.failure_detail)
    assert client.calls == []


def test_supplement_submits_the_complete_body_verbatim() -> None:
    """Review round 1 P1-8: proposed_content is the COMPLETE post-operation
    body; the adapter submits it verbatim — never prepends the stored body
    (double concatenation when the review already integrated it)."""
    client = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "existing", "version": "5"}}
    )
    full_body = "existing\n\nAdditional finding integrated by the review"
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="supplement",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "supplement",
                "title": "",
                "content": full_body,
                "target_object_id": "doc-7",
                "base_version": "5",
            },
        )
    )
    assert outcome.status == "accepted"
    update_kwargs = client.calls[1][1]
    assert update_kwargs["base_version"] == "5"
    assert update_kwargs["content"] == full_body


def test_targeted_update_without_conditional_update_support_goes_to_human_review() -> None:
    """Without probe-proven conditional updates the adapter never performs a
    version-protected targeted write."""
    client = FakeWeKnoraStore(
        conditional_update=False,
        created_objects={"doc-7": {"title": "T", "content": "existing", "version": "5"}},
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="replace",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "replace",
                "title": "T",
                "content": "New body",
                "target_object_id": "doc-7",
                "base_version": "5",
            },
        )
    )
    assert outcome.status == "human_review"
    assert outcome.failure_code == "conditional_update_unsupported"
    assert client.calls == []


def test_merge_uses_merged_content_and_review_base_version() -> None:
    client = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "existing", "version": "5"}}
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="merge",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "merge",
                "title": "T",
                "content": "",
                "merged_content": "Merged full body",
                "target_object_id": "doc-7",
                "base_version": "5",
            },
        )
    )
    assert outcome.status == "accepted"
    update_kwargs = client.calls[1][1]
    assert update_kwargs["content"] == "Merged full body"
    assert update_kwargs["base_version"] == "5"


def test_replace_version_conflict_never_overwrites() -> None:
    client = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "existing", "version": "5"}}
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="replace",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "replace",
                "title": "T",
                "content": "New body",
                "target_object_id": "doc-7",
                "base_version": "4",
            },
        )
    )
    assert outcome.status == "human_review"
    assert outcome.failure_code == "target_version_conflict"
    assert client.calls == [("knowledge_read", {"object_id": "doc-7"})]


# -- review round 1, P1-9: recovery precedes base_version validation --------


def test_completed_targeted_task_reconciles_before_base_version_check() -> None:
    # First execution updated doc-7 from v5 to v6 and recorded that result;
    # the retry still carries the review's original base_version=5. Checking
    # the base version before reconciling would misreport the completed
    # write as target_version_conflict (the reviewer's exact repro shape).
    client = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "New body", "version": "6"}}
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="replace",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "replace",
                "title": "T",
                "content": "New body",
                "target_object_id": "doc-7",
                "base_version": "5",
            },
            weknora_object_id="doc-7",
            weknora_version="6",
        )
    )
    assert outcome.status == "accepted"
    assert outcome.receipt == {
        "operation": "reconciled_existing",
        "object_id": "doc-7",
        "version": "6",
    }
    # Reconciliation proved the earlier write landed: no second update.
    assert [name for name, _ in client.calls] == ["knowledge_read"]


# -- review round 1: the lineage DTO is a pinned contract -------------------


def test_lineage_metadata_dto_is_pinned() -> None:
    from backend.services.weknora_promotion_adapter import (
        WEKNORA_LINEAGE_METADATA_FIELDS,
    )

    adapter = WeKnoraPromotionAdapter(FakeWeKnoraStore())
    case_bound = adapter._lineage_metadata({
        "promotion_id": "weknora:hermes_case_promotion:123-1:1:knowledge:0f0e0d",
        "engineer_case_id": "123-1", "client_ticket_id": "123",
        "investigation_id": "INV-1", "summary_session_id": "s", "summary_run_id": "sr",
        "review_session_id": "r", "review_run_id": "rr",
        "slack_channel_id": "C1", "slack_thread_ts": "123.456",
        "source_type": "hermes_case_promotion", "source_id": "123-1:1", "source_version": "1",
        "candidate_type": "knowledge", "decision": "new",
    })
    assert set(case_bound) == set(WEKNORA_LINEAGE_METADATA_FIELDS) | {
        "promotion_id", "candidate_type", "decision",
    }

    standalone = adapter._lineage_metadata({
        "promotion_id": "weknora:knowledge_source_review:src:v2:memory:0f0e0d",
        "engineer_case_id": None, "client_ticket_id": None,
        "source_type": "knowledge_source_review", "source_id": "src", "source_version": "v2",
        "candidate_type": "memory", "decision": "new",
    })
    # Standalone lineage: absent case fields are omitted, never empty strings.
    assert "engineer_case_id" not in standalone
    assert "client_ticket_id" not in standalone
    assert standalone["source_type"] == "knowledge_source_review"
    assert standalone["candidate_type"] == "memory"


# -- review-acceptance defect 2: readback must prove the write --------------


def _tamper_read(client: FakeWeKnoraStore, transform) -> None:
    def tampered_read(*, object_id: str) -> dict[str, Any]:
        record = client.objects[object_id]
        base = {
            "object_id": object_id,
            "version": record["version"],
            "title": record["title"],
            "content": record["content"],
        }
        return transform(base)

    client.knowledge_read = tampered_read  # type: ignore[method-assign]


def test_readback_with_stale_content_is_outcome_unknown_not_accepted() -> None:
    client = FakeWeKnoraStore()
    # Write lands in the store, but the served read returns old content and an
    # older version — the reviewer's exact repro shape.
    _tamper_read(
        client,
        lambda base: {
            **base,
            "version": str(int(base["version"]) - 1),
            "content": "old content that was never written",
        },
    )
    outcome = WeKnoraPromotionAdapter(client).execute(_task())
    assert outcome.status == "outcome_unknown"
    assert outcome.failure_code == "readback_failed"
    assert "content mismatch" in str(outcome.failure_detail)


def test_readback_version_mismatch_is_outcome_unknown() -> None:
    client = FakeWeKnoraStore()
    _tamper_read(client, lambda base: {**base, "version": str(int(base["version"]) + 50)})
    outcome = WeKnoraPromotionAdapter(client).execute(_task())
    assert outcome.status == "outcome_unknown"
    assert outcome.failure_code == "readback_failed"
    assert "version mismatch" in str(outcome.failure_detail)


def test_readback_object_mismatch_is_outcome_unknown() -> None:
    client = FakeWeKnoraStore()
    _tamper_read(client, lambda base: {**base, "object_id": "doc-999"})
    outcome = WeKnoraPromotionAdapter(client).execute(_task())
    assert outcome.status == "outcome_unknown"
    assert "object mismatch" in str(outcome.failure_detail)


def test_write_ok_readback_unreachable_is_outcome_unknown_with_object_recorded() -> None:
    client = FakeWeKnoraStore(readback_error=WeKnoraError("gone", failure_kind="timeout"))
    outcome = WeKnoraPromotionAdapter(client).execute(_task())
    assert outcome.status == "outcome_unknown"
    assert outcome.failure_code == "readback_failed"
    assert outcome.weknora_object_id == "doc-1"
    assert outcome.weknora_version == "101"


# -- review-acceptance defect 3: requeue must not blindly re-create ---------


def test_requeued_task_with_matching_object_reconciles_without_second_create() -> None:
    client = FakeWeKnoraStore(
        created_objects={"doc-1": {"title": "Title", "content": "Body", "version": "101"}}
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(weknora_object_id="doc-1", weknora_version="101")
    )
    assert outcome.status == "accepted"
    assert outcome.receipt == {
        "operation": "reconciled_existing",
        "object_id": "doc-1",
        "version": "101",
    }
    # Reconciliation proved the earlier write landed: no write of any kind.
    assert [name for name, _ in client.calls] == ["knowledge_read"]


def test_requeued_task_with_absent_object_writes_exactly_once() -> None:
    client = FakeWeKnoraStore()
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(weknora_object_id="doc-gone", weknora_version="101")
    )
    assert outcome.status == "accepted"
    names = [name for name, _ in client.calls]
    # One read proves absence, then exactly one create, then readback.
    assert names == ["knowledge_read", "knowledge_create", "knowledge_read"]
    assert names.count("knowledge_create") == 1


def test_requeued_task_with_diverged_object_goes_to_human_review() -> None:
    client = FakeWeKnoraStore(
        created_objects={"doc-1": {"title": "Title", "content": "different content", "version": "999"}}
    )
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(weknora_object_id="doc-1", weknora_version="101")
    )
    assert outcome.status == "human_review"
    assert outcome.failure_code == "reconcile_content_mismatch"
    assert [name for name, _ in client.calls] == ["knowledge_read"]


def test_requeued_task_reconcile_read_failure_stays_outcome_unknown() -> None:
    client = FakeWeKnoraStore(read_error=WeKnoraError("flaky", failure_kind="timeout"))
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(weknora_object_id="doc-1", weknora_version="101")
    )
    assert outcome.status == "outcome_unknown"
    assert outcome.failure_code == "reconcile_read_timeout"
    # No write attempted against an unverifiable known object.
    assert [name for name, _ in client.calls] == ["knowledge_read"]


# -- failure matrix (unchanged guarantees) -----------------------------------


@pytest.mark.parametrize(
    "error,expected_status,expected_code",
    [
        (WeKnoraError("miss", failure_kind="not_found"), "human_review", "target_not_found"),
        (WeKnoraError("denied", failure_kind="auth"), "failed", "weknora_auth_rejected"),
        (WeKnoraError("t", failure_kind="timeout"), "outcome_unknown", "weknora_read_timeout"),
        (WeKnoraError("t", failure_kind="transport"), "outcome_unknown", "weknora_read_transport"),
    ],
)
def test_read_failure_matrix(error, expected_status, expected_code) -> None:
    client = FakeWeKnoraStore(read_error=error)
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(
            decision="supplement",
            payload={
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "supplement",
                "title": "",
                "content": "x",
                "target_object_id": "doc-7",
                "base_version": "5",
            },
        )
    )
    assert outcome.status == expected_status
    assert outcome.failure_code == expected_code
    assert [name for name, _ in client.calls] == ["knowledge_read"]


@pytest.mark.parametrize(
    "error,expected_status,expected_code",
    [
        (WeKnoraError("denied", failure_kind="auth"), "failed", "weknora_auth_rejected"),
        (WeKnoraError("stale", failure_kind="conflict"), "human_review", "target_version_conflict"),
        (WeKnoraError("t", failure_kind="timeout"), "outcome_unknown", "weknora_write_timeout"),
        (
            WeKnoraError("boom", failure_kind="http", status_code=500),
            "outcome_unknown",
            "weknora_write_http",
        ),
        (
            WeKnoraError("bad", failure_kind="http", status_code=400),
            "failed",
            "weknora_write_http",
        ),
    ],
)
def test_write_failure_matrix(error, expected_status, expected_code) -> None:
    client = FakeWeKnoraStore(write_error=error)
    outcome = WeKnoraPromotionAdapter(client).execute(_task())
    assert outcome.status == expected_status
    assert outcome.failure_code == expected_code


def test_skill_candidate_becomes_explicit_human_review_never_a_write() -> None:
    client = FakeWeKnoraStore()
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(candidate_type="skill", decision="new")
    )
    assert outcome.status == "human_review"
    assert outcome.failure_code == "skill_change_requires_human_review"
    assert client.calls == []


# ---------------------------------------------------------------------------
# stage6-weknora-agent-bridge: publish pass-through + bounded readiness poll
# ---------------------------------------------------------------------------

class _ReadinessClient:
    """Records writes and replays a scripted readback lifecycle."""

    def __init__(self, reads: list[dict] | None = None) -> None:
        self.reads = list(reads or [])
        self.write_status: list = []
        self.read_count = 0

    def knowledge_create(self, *, title, content, idempotency_key, metadata=None, status=None, **kw):
        self.write_status.append(status)
        receipt = {"object_id": "doc-1", "version": "1", "receipt": {}}
        if status:
            receipt["requested_status"] = status  # real-client contract
        return receipt

    def knowledge_update(self, *, object_id, base_version, title, content, idempotency_key, metadata=None, status=None, **kw):
        self.write_status.append(status)
        return {"object_id": object_id, "version": "2", "receipt": {}}

    def knowledge_read(self, *, object_id):
        self.read_count += 1
        if self.read_count > len(self.reads):
            return self.reads[-1]
        return self.reads[self.read_count - 1]


def _ready_task() -> dict:
    return _task(payload={
        "schema_version": "v1", "candidate_type": "knowledge", "decision": "new",
        "title": "Title", "content": "Body",
    })


def test_adapter_passes_publish_status_to_write() -> None:
    client = _ReadinessClient([{"object_id": "doc-1", "version": "1", "title": "Title", "content": "Body",
                                 "parse_status": "pending", "enable_status": "disabled"}])
    adapter = WeKnoraPromotionAdapter(client)
    outcome = adapter.execute(_ready_task())
    assert client.write_status == ["publish"]
    assert outcome.status == "accepted"
    assert outcome.receipt["requested_status"] == "publish"
    assert outcome.receipt["lifecycle_at_accept"] == {"parse_status": "pending", "enable_status": "disabled"}


def test_adapter_can_disable_publish() -> None:
    client = _ReadinessClient([{"object_id": "doc-1", "version": "1", "title": "Title", "content": "Body"}])
    adapter = WeKnoraPromotionAdapter(client, publish_on_write=False)
    adapter.execute(_ready_task())
    assert client.write_status == [None]


def test_wait_until_ready_succeeds_after_pending() -> None:
    client = _ReadinessClient([
        {"parse_status": "pending", "enable_status": "disabled", "version": "1"},
        {"parse_status": "processing", "enable_status": "disabled", "version": "1"},
        {"parse_status": "completed", "enable_status": "enabled", "version": "1"},
    ])
    result = weknora_wait_until_ready(client, "doc-1", interval_seconds=0, sleep=lambda s: None)
    assert result["ready"] is True
    assert result["attempts_used"] == 3


def test_wait_until_ready_times_out_fail_closed() -> None:
    client = _ReadinessClient([
        {"parse_status": "pending", "enable_status": "disabled", "version": "1"},
    ])
    with pytest.raises(WeKnoraError) as excinfo:
        weknora_wait_until_ready(client, "doc-1", max_attempts=3, interval_seconds=0, sleep=lambda s: None)
    assert excinfo.value.failure_kind == "timeout"


def test_wait_until_ready_fails_fast_on_parse_failed() -> None:
    client = _ReadinessClient([
        {"parse_status": "failed", "enable_status": "disabled", "version": "1"},
    ])
    with pytest.raises(WeKnoraError) as excinfo:
        weknora_wait_until_ready(client, "doc-1", interval_seconds=0, sleep=lambda s: None)
    assert "parse failed" in str(excinfo.value)


def test_wait_until_ready_retries_transient_transport() -> None:
    class FlakyClient(_ReadinessClient):
        def __init__(self):
            super().__init__([{"parse_status": "completed", "enable_status": "enabled", "version": "1"}])
            self.transport_errors = 1

        def knowledge_read(self, *, object_id):
            if self.transport_errors > 0:
                self.transport_errors -= 1
                raise WeKnoraError("boom", failure_kind="transport")
            return super().knowledge_read(object_id=object_id)

    result = weknora_wait_until_ready(FlakyClient(), "doc-1", interval_seconds=0, sleep=lambda s: None)
    assert result["ready"] is True


def test_wait_until_ready_non_transient_error_raises_immediately() -> None:
    class AuthFailClient(_ReadinessClient):
        def knowledge_read(self, *, object_id):
            raise WeKnoraError("denied", failure_kind="auth")

    with pytest.raises(WeKnoraError) as excinfo:
        weknora_wait_until_ready(AuthFailClient(), "doc-1", interval_seconds=0, sleep=lambda s: None)
    assert excinfo.value.failure_kind == "auth"

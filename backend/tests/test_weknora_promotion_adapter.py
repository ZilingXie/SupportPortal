from __future__ import annotations

from typing import Any

import pytest

from backend.services.weknora_client import WeKnoraError
from backend.services.weknora_promotion_adapter import WeKnoraPromotionAdapter


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
    ) -> None:
        self.memory_identity_value = memory_identity
        self.read_error = read_error
        self.write_error = write_error
        self.readback_error = readback_error
        self.objects: dict[str, dict[str, Any]] = dict(created_objects or {})
        self.next_version = 100
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def has_memory_identity(self) -> bool:
        return bool(self.memory_identity_value)

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

    def memory_query(self, *, query: str) -> list[dict[str, Any]]:
        self.calls.append(("memory_query", {"query": query}))
        if self.read_error or self.readback_error:
            raise self.read_error or self.readback_error
        return [
            {"object_id": object_id, "version": record["version"], "title": record["title"], "content": record["content"]}
            for object_id, record in self.objects.items()
            if query in (object_id, record.get("title", ""))
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


def test_supplement_without_base_version_appends_at_current_version() -> None:
    client = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "existing", "version": "5"}}
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
    assert outcome.status == "accepted"
    update_kwargs = client.calls[1][1]
    assert update_kwargs["base_version"] == "5"
    assert update_kwargs["content"] == "existing\n\nAdditional finding"


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


def test_unclassifiable_candidate_is_rejected() -> None:
    client = FakeWeKnoraStore()
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(candidate_type="skill", decision="new")
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "invalid_candidate"

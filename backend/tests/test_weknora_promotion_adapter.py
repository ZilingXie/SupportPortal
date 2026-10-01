from __future__ import annotations

from typing import Any

import pytest

from backend.services.weknora_client import WeKnoraError
from backend.services.weknora_promotion_adapter import WeKnoraPromotionAdapter


class FakeWeKnoraClient:
    def __init__(
        self,
        *,
        memory_identity: str = "hermes-service",
        read_result: dict[str, Any] | None = None,
        read_error: WeKnoraError | None = None,
        write_result: dict[str, Any] | None = None,
        write_error: WeKnoraError | None = None,
        readback_error: WeKnoraError | None = None,
        readback_result: dict[str, Any] | None = None,
    ) -> None:
        self.memory_identity_value = memory_identity
        self.read_result = read_result
        self.read_error = read_error
        self.write_result = write_result or {"object_id": "doc-1", "version": "2", "receipt": {"ok": True}}
        self.write_error = write_error
        self.readback_error = readback_error
        self.readback_result = readback_result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def has_memory_identity(self) -> bool:
        return bool(self.memory_identity_value)

    def knowledge_read(self, *, object_id: str) -> dict[str, Any]:
        self.calls.append(("knowledge_read", {"object_id": object_id}))
        if self.read_error or self.readback_error:
            raise self.read_error or self.readback_error
        return self.read_result or {
            "object_id": object_id,
            "version": "5",
            "title": "Existing",
            "content": "existing content",
        }

    def memory_query(self, *, query: str) -> list[dict[str, Any]]:
        self.calls.append(("memory_query", {"query": query}))
        if self.read_error or self.readback_error:
            raise self.read_error or self.readback_error
        return [
            {
                "object_id": query,
                "version": "5",
                "title": "Existing",
                "content": "existing memory",
            }
        ]

    def knowledge_create(self, *, title: str, content: str, idempotency_key: str, **kwargs) -> dict[str, Any]:
        self.calls.append(("knowledge_create", {"title": title, "content": content, "key": idempotency_key}))
        if self.write_error:
            raise self.write_error
        return self.write_result

    def knowledge_update(self, *, object_id: str, base_version: str, title: str, content: str, idempotency_key: str, **kwargs) -> dict[str, Any]:
        self.calls.append(
            ("knowledge_update", {"object_id": object_id, "base_version": base_version, "content": content, "key": idempotency_key})
        )
        if self.write_error:
            raise self.write_error
        return self.write_result

    def memory_create(self, *, content: str, idempotency_key: str, **kwargs) -> dict[str, Any]:
        self.calls.append(("memory_create", {"content": content, "key": idempotency_key}))
        if self.write_error:
            raise self.write_error
        return self.write_result

    def memory_update(self, *, object_id: str, base_version: str, content: str, idempotency_key: str, **kwargs) -> dict[str, Any]:
        self.calls.append(("memory_update", {"object_id": object_id, "base_version": base_version, "content": content, "key": idempotency_key}))
        if self.write_error:
            raise self.write_error
        return self.write_result


def _task(
    *,
    candidate_type: str = "knowledge",
    decision: str = "new",
    payload: dict[str, Any] | None = None,
    promotion_id: str = "weknora:hermes_case_promotion:123-1:1:knowledge",
) -> dict[str, Any]:
    return {
        "promotion_id": promotion_id,
        "candidate_type": candidate_type,
        "decision": decision,
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
    client = FakeWeKnoraClient()
    outcome = WeKnoraPromotionAdapter(client).execute(_task(decision="no_change"))
    assert outcome.status == "accepted"
    assert outcome.receipt == {"operation": "no_change", "decision": "no_change"}
    assert client.calls == []


def test_human_review_decision_does_not_write() -> None:
    client = FakeWeKnoraClient()
    outcome = WeKnoraPromotionAdapter(client).execute(_task(decision="human_review"))
    assert outcome.status == "human_review"
    assert client.calls == []


def test_incomplete_review_output_fails_without_write() -> None:
    client = FakeWeKnoraClient()
    outcome = WeKnoraPromotionAdapter(client).execute(_task(payload={"title": "", "content": ""}))
    assert outcome.status == "failed"
    assert outcome.failure_code == "invalid_candidate"
    assert client.calls == []


def test_new_knowledge_write_and_readback_accepted() -> None:
    client = FakeWeKnoraClient()
    outcome = WeKnoraPromotionAdapter(client).execute(_task())
    assert outcome.status == "accepted"
    assert outcome.weknora_object_id == "doc-1"
    assert outcome.weknora_version == "2"
    assert [name for name, _ in client.calls] == ["knowledge_create", "knowledge_read"]


def test_memory_candidate_without_shared_identity_goes_to_human_review() -> None:
    client = FakeWeKnoraClient(memory_identity="")
    outcome = WeKnoraPromotionAdapter(client).execute(_task(candidate_type="memory"))
    assert outcome.status == "human_review"
    assert outcome.failure_code == "memory_identity_not_configured"
    assert client.calls == []


def test_supplement_reads_current_then_appends_and_updates_at_current_version() -> None:
    client = FakeWeKnoraClient()
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
    assert [name for name, _ in client.calls] == ["knowledge_read", "knowledge_update", "knowledge_read"]
    update_kwargs = client.calls[1][1]
    assert update_kwargs["object_id"] == "doc-7"
    assert update_kwargs["base_version"] == "5"
    assert update_kwargs["content"] == "existing content\n\nAdditional finding"


def test_replace_version_conflict_never_overwrites() -> None:
    client = FakeWeKnoraClient()
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
    assert [name for name, _ in client.calls] == ["knowledge_read"]


def test_merge_uses_merged_content_and_current_version() -> None:
    client = FakeWeKnoraClient()
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
    client = FakeWeKnoraClient(read_error=error)
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
    client = FakeWeKnoraClient(write_error=error)
    outcome = WeKnoraPromotionAdapter(client).execute(_task())
    assert outcome.status == expected_status
    assert outcome.failure_code == expected_code


def test_write_ok_readback_failure_is_outcome_unknown_with_object_recorded() -> None:
    client = FakeWeKnoraClient(readback_error=WeKnoraError("gone", failure_kind="timeout"))
    outcome = WeKnoraPromotionAdapter(client).execute(_task())
    assert outcome.status == "outcome_unknown"
    assert outcome.failure_code == "readback_failed"
    assert outcome.weknora_object_id == "doc-1"
    assert outcome.weknora_version == "2"
    # The write itself succeeded once; only the readback failed and no second
    # write was attempted.
    assert [name for name, _ in client.calls] == ["knowledge_create", "knowledge_read"]


def test_skill_candidate_becomes_explicit_human_review_never_a_write() -> None:
    client = FakeWeKnoraClient()
    outcome = WeKnoraPromotionAdapter(client).execute(
        _task(candidate_type="skill", decision="new")
    )
    assert outcome.status == "human_review"
    assert outcome.failure_code == "skill_change_requires_human_review"
    assert client.calls == []

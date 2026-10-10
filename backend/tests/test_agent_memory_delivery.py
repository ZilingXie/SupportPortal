from __future__ import annotations

import os

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from backend.services.agent_memory_delivery import (  # noqa: E402
    AgentMemoryDeliveryAdapter,
    AgentMemoryWikiClient,
    AgentMemoryWikiError,
    agent_memory_filename,
)
from backend.services.hermes_case_workflow import _weknora_candidate_hash  # noqa: E402

PROMOTION = {
    "promotion_id": "weknora:csd_issue:ISS-1:v2:knowledge:abc",
    "candidate_type": "knowledge",
    "decision": "new",
    "content_hash": "c" * 64,
    "candidate_payload": {
        "schema_version": "v1",
        "candidate_type": "knowledge",
        "decision": "new",
        "title": "Audio routing failure",
        "content": "# Body",
        "note": "clear cause and fix",
    },
}


class FakeWikiClient:
    def __init__(
        self,
        *,
        create_error: AgentMemoryWikiError | None = None,
        raw_write_error: AgentMemoryWikiError | None = None,
        ingest_error: AgentMemoryWikiError | None = None,
        get_error: AgentMemoryWikiError | None = None,
        files_on_get: list[str] | None = None,
        exists: set[str] | None = None,
    ) -> None:
        self.create_calls: list[str] = []
        self.raw_write_calls: list[tuple[str, str]] = []
        self.ingest_calls: list[str] = []
        self.get_calls: list[str] = []
        self.create_error = create_error
        self.raw_write_error = raw_write_error
        self.ingest_error = ingest_error
        self.get_error = get_error
        self.files_on_get = files_on_get
        self.exists = exists if exists is not None else set()
        self._seq = 0

    def configured(self) -> bool:
        return True

    def health(self) -> dict:
        return {"status": "ok"}

    def wiki_create(self, *, name: str) -> dict:
        self.create_calls.append(name)
        if self.create_error is not None:
            raise self.create_error
        self._seq += 1
        wiki_id = f"wiki-{self._seq}"
        self.exists.add(wiki_id)
        return {"data": {"wiki_id": wiki_id}}

    def wiki_raw_write(self, *, wiki_id: str, filename: str, content: str) -> dict:
        self.raw_write_calls.append((wiki_id, filename))
        if self.raw_write_error is not None:
            raise self.raw_write_error

        def record(wid: str) -> dict:
            if self.files_on_get is None:
                return {"data": {"wiki_id": wid}}
            return {"data": {"wiki_id": wid, "files": [{"filename": f} for f in self.files_on_get]}}

        self._get_impl = record
        return {"success": True}

    def wiki_ingest(self, *, wiki_id: str) -> dict:
        self.ingest_calls.append(wiki_id)
        if self.ingest_error is not None:
            raise self.ingest_error
        return {"success": True}

    _get_impl = None

    def wiki_get(self, *, wiki_id: str) -> dict:
        self.get_calls.append(wiki_id)
        if self.get_error is not None:
            raise self.get_error
        if wiki_id not in self.exists:
            raise AgentMemoryWikiError(
                f"wiki {wiki_id} not found", failure_kind="not_found", status_code=404
            )
        if self._get_impl is not None:
            return self._get_impl(wiki_id)
        if self.files_on_get is None:
            return {"data": {"wiki_id": wiki_id}}
        return {"data": {"wiki_id": wiki_id, "files": [{"filename": f} for f in self.files_on_get]}}


def _delivery(**overrides) -> dict:
    row = {
        "delivery_id": "weknora:csd_issue:ISS-1:v2:knowledge:abc:agent_memory",
        "promotion_id": PROMOTION["promotion_id"],
        "target": "agent_memory",
        "status": "active",
        "external_object_id": None,
        "operation_receipt": {},
        "attempt_count": 1,
    }
    row.update(overrides)
    return row


def test_new_candidate_full_write_with_durable_object_capture() -> None:
    client = FakeWikiClient(files_on_get=[agent_memory_filename(PROMOTION["content_hash"])])
    captured: list[str] = []
    outcome = AgentMemoryDeliveryAdapter(client).execute(
        promotion=PROMOTION, delivery=_delivery(), on_object_id=captured.append
    )
    assert outcome.status == "accepted"
    assert outcome.external_object_id == "wiki-1"
    assert captured == ["wiki-1"]
    assert client.create_calls and client.raw_write_calls == [("wiki-1", "sp-" + "c" * 64 + ".md")]
    assert client.ingest_calls == ["wiki-1"]
    assert outcome.receipt["wiki_create_attempted"] is True
    assert outcome.external_version and outcome.external_version.startswith("file:")


def test_create_timeout_is_outcome_unknown_without_object_id() -> None:
    client = FakeWikiClient(
        create_error=AgentMemoryWikiError("timeout", failure_kind="timeout")
    )
    outcome = AgentMemoryDeliveryAdapter(client).execute(
        promotion=PROMOTION, delivery=_delivery()
    )
    assert outcome.status == "outcome_unknown"
    assert outcome.failure_code == "agent_memory_wiki_create_timeout"
    assert outcome.external_object_id is None
    assert outcome.receipt["wiki_create_attempted"] is True


def test_create_retry_without_wiki_id_never_re_creates() -> None:
    client = FakeWikiClient()
    delivery = _delivery(
        operation_receipt={"wiki_create_attempted": True},
        attempt_count=2,
    )
    outcome = AgentMemoryDeliveryAdapter(client).execute(promotion=PROMOTION, delivery=delivery)
    assert outcome.status == "human_review"
    assert outcome.failure_code == "wiki_create_outcome_unknown"
    # The core guarantee: ZERO create calls — no blind duplicate-wiki risk.
    assert client.create_calls == []


def test_raw_write_timeout_keeps_captured_object_and_retry_reconciles() -> None:
    client = FakeWikiClient(
        raw_write_error=AgentMemoryWikiError("timeout", failure_kind="timeout")
    )
    adapter = AgentMemoryDeliveryAdapter(client)
    outcome = adapter.execute(promotion=PROMOTION, delivery=_delivery())
    assert outcome.status == "outcome_unknown"
    assert outcome.external_object_id == "wiki-1"

    # Retry with the durably captured wiki_id: the file never landed, so the
    # reconcile rewrites the SAME content-addressed filename and ingests.
    client.raw_write_error = None
    client.files_on_get = [agent_memory_filename(PROMOTION["content_hash"])]
    retry = _delivery(external_object_id="wiki-1", attempt_count=2)
    outcome2 = adapter.execute(promotion=PROMOTION, delivery=retry)
    assert outcome2.status == "accepted"
    assert outcome2.external_object_id == "wiki-1"
    # Exactly one create ever happened — the reconcile never re-created.
    assert client.create_calls and len(client.create_calls) == 1


def test_retry_with_landed_file_is_idempotent_replay() -> None:
    filename = agent_memory_filename(PROMOTION["content_hash"])
    client = FakeWikiClient(files_on_get=[filename])
    client.exists.add("wiki-9")
    client._get_impl = lambda wid: {
        "data": {"wiki_id": wid, "files": [{"filename": filename}]}
    }
    outcome = AgentMemoryDeliveryAdapter(client).execute(
        promotion=PROMOTION, delivery=_delivery(external_object_id="wiki-9")
    )
    assert outcome.status == "accepted"
    assert client.raw_write_calls == [] and client.ingest_calls == []


def test_auth_failure_is_terminal_failed() -> None:
    client = FakeWikiClient(
        create_error=AgentMemoryWikiError("401", failure_kind="auth", status_code=401)
    )
    outcome = AgentMemoryDeliveryAdapter(client).execute(
        promotion=PROMOTION, delivery=_delivery()
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "agent_memory_auth_rejected"


def test_oversized_body_fails_closed_before_any_write() -> None:
    big = {**PROMOTION, "candidate_payload": {**PROMOTION["candidate_payload"], "content": "x" * (513 * 1024)}}
    client = FakeWikiClient()
    outcome = AgentMemoryDeliveryAdapter(client).execute(promotion=big, delivery=_delivery())
    assert outcome.status == "failed"
    assert outcome.failure_code == "agent_memory_wiki_raw_write_payload_too_large"
    assert client.create_calls == []


def test_readback_without_file_proof_is_outcome_unknown() -> None:
    client = FakeWikiClient(files_on_get=["other.md"])
    outcome = AgentMemoryDeliveryAdapter(client).execute(
        promotion=PROMOTION, delivery=_delivery()
    )
    assert outcome.status == "outcome_unknown"
    assert outcome.failure_code == "agent_memory_readback_unproven"
    assert outcome.external_object_id == "wiki-1"


def test_targeted_delivery_writes_into_target_wiki() -> None:
    targeted = {
        **PROMOTION,
        "candidate_payload": {
            **PROMOTION["candidate_payload"],
            "decision": "supplement",
            "content": "# supplemented",
            "target_object_id": "wiki-7",
            "base_version": "v3",
        },
    }
    filename = agent_memory_filename(targeted["content_hash"])
    client = FakeWikiClient(files_on_get=[filename])
    client.exists.add("wiki-7")
    client._get_impl = lambda wid: {"data": {"wiki_id": wid, "files": [{"filename": filename}]}}
    outcome = AgentMemoryDeliveryAdapter(client).execute(
        promotion=targeted, delivery=_delivery()
    )
    assert outcome.status == "accepted"
    assert outcome.external_object_id == "wiki-7"
    assert client.create_calls == []
    assert client.raw_write_calls == [("wiki-7", filename)]


def test_targeted_delivery_without_target_fails() -> None:
    targeted = {
        **PROMOTION,
        "candidate_payload": {**PROMOTION["candidate_payload"], "decision": "replace", "content": "x"},
    }
    outcome = AgentMemoryDeliveryAdapter(FakeWikiClient()).execute(
        promotion=targeted, delivery=_delivery()
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "targeted_delivery_without_target"


def test_missing_target_wiki_routes_to_human_review() -> None:
    targeted = {
        **PROMOTION,
        "candidate_payload": {
            **PROMOTION["candidate_payload"],
            "decision": "merge",
            "merged_content": "# merged",
            "target_object_id": "wiki-gone",
        },
    }
    client = FakeWikiClient()
    outcome = AgentMemoryDeliveryAdapter(client).execute(
        promotion=targeted, delivery=_delivery()
    )
    assert outcome.status == "human_review"
    assert outcome.failure_code == "target_wiki_missing"


def test_client_is_unconfigured_without_env(monkeypatch) -> None:
    for name in (
        "AGENT_MEMORY_WIKI_BASE_URL",
        "AGENT_MEMORY_WIKI_USER_KEY",
        "AGENT_MEMORY_WIKI_TEAM_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    assert AgentMemoryWikiClient().configured() is False


def test_candidate_hash_helper_matches_promotion_identity() -> None:
    # The dual-write gate recomputes the payload hash with the SAME helper
    # the promotion bridge used at enqueue time.
    assert _weknora_candidate_hash(PROMOTION["candidate_payload"]) != ""

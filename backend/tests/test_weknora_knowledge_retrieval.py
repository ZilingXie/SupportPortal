"""stage6-weknora-agent-bridge: unified retrieval service + Agent bridge."""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

import pytest

from backend.services.weknora_client import WeKnoraError
from backend.services.weknora_knowledge_retrieval import (
    RETRIEVAL_SOURCE_ESCALATE,
    RETRIEVAL_SOURCE_FALLBACK,
    RETRIEVAL_SOURCE_WEKNORA,
    retrieve_knowledge_evidence,
)


class FakeWeKnora:
    def __init__(self, *, hits=None, reads=None, search_error=None):
        self.hits = hits or []
        self.reads = dict(reads or {})
        self.search_error = search_error

    def knowledge_search(self, *, query, top_k):
        if self.search_error:
            raise self.search_error
        return self.hits[:top_k]

    def knowledge_read(self, *, object_id):
        record = self.reads.get(object_id)
        if record is None:
            raise WeKnoraError("missing", failure_kind="invalid_response")
        if isinstance(record, Exception):
            raise record
        return record


READY_READ = {
    "object_id": "doc-1",
    "version": "3",
    "title": "V2V subtitle loss",
    "content": "Root cause: shutdown is not dependency-aware; drain before close.",
    "parse_status": "completed",
    "enable_status": "enabled",
    "payload": {"data": {"metadata": {"version": 3, "lineage": {
        "source_type": "csd_issue", "source_id": "CSD-79994",
        "content_hash": "ab" * 32, "promotion_id": "weknora:p:1",
    }}}},
}


class FakeAgentMemory:
    def __init__(self, result=None, error=None):
        self.result = result if result is not None else {"hits": []}
        self.error = error

    def search_knowledge(self, query, *, top_k=None):
        if self.error:
            raise self.error
        return self.result


def test_weknora_ready_hit_returns_traceable_evidence() -> None:
    out = retrieve_knowledge_evidence(
        "v2v subtitle",
        weknora_client=FakeWeKnora(hits=[{"object_id": "doc-1"}], reads={"doc-1": READY_READ}),
    )
    assert out["retrieval_source"] == RETRIEVAL_SOURCE_WEKNORA
    (entry,) = out["results"]
    assert entry["weknora_object_id"] == "doc-1"
    assert entry["content_version"] == "3"
    assert entry["content_hash"] == "ab" * 32
    assert entry["source_type"] == "csd_issue"
    assert entry["source_id"] == "CSD-79994"
    assert "dependency-aware" in entry["evidence"]


def test_draft_object_is_skipped_not_errored() -> None:
    draft = {**READY_READ, "parse_status": "draft", "enable_status": "disabled"}
    out = retrieve_knowledge_evidence(
        "v2v subtitle",
        weknora_client=FakeWeKnora(hits=[{"object_id": "doc-1"}], reads={"doc-1": draft}),
    )
    assert out["retrieval_source"] == RETRIEVAL_SOURCE_WEKNORA  # valid empty (C6)
    assert out["results"] == []


def test_missing_lifecycle_fields_are_not_ready() -> None:
    unknown = {k: v for k, v in READY_READ.items() if k not in ("parse_status", "enable_status")}
    out = retrieve_knowledge_evidence(
        "q", weknora_client=FakeWeKnora(hits=[{"object_id": "doc-1"}], reads={"doc-1": unknown})
    )
    assert out["results"] == []


@pytest.mark.parametrize("kind,status", [
    ("transport", None), ("timeout", None), ("auth", None),
    ("http", 503), ("http", 500), ("invalid_response", None),
])
def test_weknora_unavailable_falls_back_to_agent_memory(kind, status) -> None:
    error = WeKnoraError("boom", failure_kind=kind, status_code=status)
    am = FakeAgentMemory(result={"hits": [{"title": "AM doc", "excerpt": "am evidence", "wiki_id": "w-1"}]})
    out = retrieve_knowledge_evidence(
        "q", weknora_client=FakeWeKnora(search_error=error), agent_memory_client=am
    )
    assert out["retrieval_source"] == RETRIEVAL_SOURCE_FALLBACK
    assert out["fallback_reason"].startswith("weknora_unavailable")
    (entry,) = out["results"]
    assert entry["source_type"] == "agent_memory_wiki"
    assert entry["evidence"] == "am evidence"


def test_malformed_per_hit_readback_falls_back_to_agent_memory() -> None:
    """A search hit whose detail response is malformed is unavailable, not empty."""
    am = FakeAgentMemory(result={"hits": [{"title": "AM doc", "excerpt": "fallback", "wiki_id": "w-2"}]})
    out = retrieve_knowledge_evidence(
        "q",
        weknora_client=FakeWeKnora(
            hits=[{"object_id": "doc-1"}],
            reads={"doc-1": WeKnoraError("malformed", failure_kind="invalid_response")},
        ),
        agent_memory_client=am,
    )
    assert out["retrieval_source"] == RETRIEVAL_SOURCE_FALLBACK
    assert out["results"][0]["evidence"] == "fallback"
    assert "invalid_response" in out["fallback_reason"]


def test_weknora_4xx_is_raised_not_hidden_as_fallback() -> None:
    error = WeKnoraError("nope", failure_kind="http", status_code=404)
    with pytest.raises(WeKnoraError):
        retrieve_knowledge_evidence(
            "q", weknora_client=FakeWeKnora(search_error=error), agent_memory_client=FakeAgentMemory()
        )


def test_both_sides_down_escalates_human() -> None:
    error = WeKnoraError("boom", failure_kind="transport")
    out = retrieve_knowledge_evidence(
        "q",
        weknora_client=FakeWeKnora(search_error=error),
        agent_memory_client=FakeAgentMemory(error=RuntimeError("down")),
    )
    assert out["retrieval_source"] == RETRIEVAL_SOURCE_ESCALATE
    assert "agent_memory_unavailable" in out["fallback_reason"]
    assert out["results"] == []


def test_no_fallback_configured_escalates() -> None:
    error = WeKnoraError("boom", failure_kind="transport")
    out = retrieve_knowledge_evidence("q", weknora_client=FakeWeKnora(search_error=error))
    assert out["retrieval_source"] == RETRIEVAL_SOURCE_ESCALATE
    assert "no fallback configured" in out["fallback_reason"]


def test_empty_query_escalates() -> None:
    out = retrieve_knowledge_evidence("  ", weknora_client=FakeWeKnora())
    assert out["retrieval_source"] == RETRIEVAL_SOURCE_ESCALATE


# -- Agent bridge -----------------------------------------------------------


class _Packet(dict):
    pass


def _agent_run(packet, **kwargs):
    from backend.services.engineer_execute_agent import run_internal_knowledge_search_subagent

    return run_internal_knowledge_search_subagent(
        {"task_id": "t-1"}, packet, now_value="2026-10-19T00:00:00+00:00", **kwargs
    )


def test_agent_bridge_consumes_weknora_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.services.engineer_execute_agent._default_weknora_retrieval_client",
        lambda: FakeWeKnora(hits=[{"object_id": "doc-1"}], reads={"doc-1": READY_READ}),
    )
    result = _agent_run({"question": "v2v subtitle loss"})
    assert result["status"] == "succeeded"
    kinds = [ref["kind"] for ref in result["evidence_refs"]]
    assert "knowledge_weknora" in kinds
    weknora_ref = next(ref for ref in result["evidence_refs"] if ref["kind"] == "knowledge_weknora")
    assert weknora_ref["weknora_object_id"] == "doc-1"
    assert weknora_ref["content_version"] == "3"
    assert weknora_ref["source_id"] == "CSD-79994"


def test_agent_bridge_escalation_blocks_with_missing_information(monkeypatch) -> None:
    error = WeKnoraError("boom", failure_kind="transport")
    monkeypatch.setattr(
        "backend.services.engineer_execute_agent._default_weknora_retrieval_client",
        lambda: FakeWeKnora(search_error=error),
    )
    result = _agent_run({"question": "q"})  # no fallback client -> escalate
    assert result["status"] == "blocked"
    assert any("escalated" in m for m in result["missing_information"])


def test_agent_bridge_without_weknora_keeps_v1_behavior(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.services.engineer_execute_agent._default_weknora_retrieval_client",
        lambda: None,
    )
    result = _agent_run({"rag_result": {"candidate_answer": "legacy", "sources": ["s1"]}})
    assert result["status"] == "succeeded"
    kinds = [ref["kind"] for ref in result["evidence_refs"]]
    assert "rag_candidate" in kinds and "knowledge_weknora" not in kinds

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


# -- Real-entry chain regressions (review B3) -------------------------------


class _FailingWeKnora:
    def knowledge_search(self, *, query, top_k):
        raise WeKnoraError("boom", failure_kind="transport")

    def knowledge_read(self, *, object_id):
        raise AssertionError("read should not be called when search failed")


class _WorkingAM:
    def search_knowledge(self, query, *, top_k=None):
        return {"hits": [{"title": "AM hit", "excerpt": "am evidence", "wiki_id": "w-9"}]}


def _allowlisted_task() -> dict:
    return {
        "task_id": "ks-1",
        "skill": "internal_knowledge_search",
        "depends_on": [],
    }


def test_real_entry_weknora_down_am_up_returns_fallback() -> None:
    from backend.services.engineer_execute_agent import run_allowlisted_subagent

    result = run_allowlisted_subagent(
        _allowlisted_task(),
        {"question": "q"},
        now_value="2026-10-19T00:00:00+00:00",
        weknora_client=_FailingWeKnora(),
        agent_memory_client=_WorkingAM(),
    )
    assert result["status"] == "succeeded"
    kinds = [ref["kind"] for ref in result["evidence_refs"]]
    assert "knowledge_agent_memory_fallback" in kinds
    assert "knowledge_weknora" not in kinds


def test_real_entry_both_down_returns_blocked() -> None:
    from backend.services.engineer_execute_agent import run_allowlisted_subagent

    class BrokenAM:
        def search_knowledge(self, query, *, top_k=None):
            raise RuntimeError("down")

    result = run_allowlisted_subagent(
        _allowlisted_task(),
        {"question": "q"},
        now_value="2026-10-19T00:00:00+00:00",
        weknora_client=_FailingWeKnora(),
        agent_memory_client=BrokenAM(),
    )
    assert result["status"] == "blocked"
    assert any("escalated" in m for m in result["missing_information"])


def test_execute_engineer_plan_passes_retrieval_clients() -> None:
    from backend.services.engineer_execute_agent import execute_engineer_plan

    plan = {
        "plan_id": "plan-1",
        "tasks": [_allowlisted_task()],
    }
    execution = execute_engineer_plan(
        active_plan=plan,
        summary_packet={"packet_id": "pkt-1", "question": "q"},
        now_value="2026-10-19T00:00:00+00:00",
        weknora_client=_FailingWeKnora(),
        agent_memory_client=_WorkingAM(),
    )
    results = execution.get("task_results") or []
    assert results, "expected at least one task result"
    knowledge_results = [r for r in results if r.get("skill") == "internal_knowledge_search"]
    assert knowledge_results
    kinds = [ref["kind"] for ref in knowledge_results[0].get("evidence_refs") or []]
    assert "knowledge_agent_memory_fallback" in kinds


# -- B3 residual: DEFAULT-constructed AgentMemory fallback (no injection) ----


def test_default_agent_memory_client_uses_configured_method(monkeypatch) -> None:
    """The default constructor must gate on the REAL client API
    (``configured()``, not ``is_configured()``): a wrong method name raises
    AttributeError, the helper swallows it, and every real entry silently
    loses its fallback (review finding)."""
    import backend.services.engineer_execute_agent as agent_mod
    import backend.services.agent_memory_delivery as am_mod

    class EnvConfiguredAM(am_mod.AgentMemoryWikiClient):
        def __init__(self):  # bypass env parsing
            pass

        def configured(self) -> bool:
            return True

        def is_configured(self) -> bool:
            raise AssertionError("wrong gate method; must be configured()")

    monkeypatch.setattr(am_mod, "AgentMemoryWikiClient", EnvConfiguredAM)
    client = agent_mod._default_agent_memory_client()
    assert isinstance(client, EnvConfiguredAM)


def test_real_entry_default_fallback_without_injection(monkeypatch) -> None:
    """WeKnora down + AgentMemory configured IN THE ENVIRONMENT (via the
    default-constructor path) -> the real allowlisted entry falls back."""
    import backend.services.engineer_execute_agent as agent_mod
    import backend.services.agent_memory_delivery as am_mod

    class EnvAM(am_mod.AgentMemoryWikiClient):
        def __init__(self):
            pass

        def configured(self) -> bool:
            return True

        def search_knowledge(self, query, *, top_k=None):
            return {"hits": [{"title": "default AM", "excerpt": "default am evidence", "wiki_id": "w-7"}]}

    monkeypatch.setattr(am_mod, "AgentMemoryWikiClient", EnvAM)
    result = agent_mod.run_allowlisted_subagent(
        _allowlisted_task(),
        {"question": "q"},
        now_value="2026-10-19T00:00:00+00:00",
        weknora_client=_FailingWeKnora(),
        # NOTE: no agent_memory_client — the default path must supply it
    )
    assert result["status"] == "succeeded"
    kinds = [ref["kind"] for ref in result["evidence_refs"]]
    assert "knowledge_agent_memory_fallback" in kinds


# -- B4 residual: Review uses the SHARED readiness primitive ---------------


def _review_packet() -> dict:
    return {"candidates": [{"candidate_id": "c-1", "statement": "s"}]}


class _ReviewWeKnora:
    def __init__(self, reads):
        self._reads = reads
        self.configured = lambda: True

    def search(self, query):
        return [{"object_id": "doc-1", "title": "T"}]

    def read(self, object_id):
        record = self._reads[object_id]
        if isinstance(record, Exception):
            raise record
        return record


def _collect(client):
    from backend.services.hermes_knowledge_workflow import _collect_weknora_evidence

    knowledge, memory, k_ok, m_ok = _collect_weknora_evidence(client, _review_packet())
    return knowledge["c-1"], k_ok


def test_review_agent_ready_marks_enabled_entry() -> None:
    full = {
        "content": "body", "content_version": "2", "lineage": {},
        "parse_status": "completed", "enable_status": "enabled",
    }
    entries, ok = _collect(_ReviewWeKnora({"doc-1": full}))
    assert ok is True
    assert entries[0]["agent_ready"] is True


def test_review_agent_ready_false_for_disabled_or_missing() -> None:
    disabled = {"content": "b", "content_version": "1", "lineage": {},
                "parse_status": "completed", "enable_status": "disabled"}
    entries, ok = _collect(_ReviewWeKnora({"doc-1": disabled}))
    assert ok is True  # review still sees the conflict candidate
    assert entries[0]["agent_ready"] is False

    missing = {"content": "b", "content_version": "1", "lineage": {}}
    entries2, _ = _collect(_ReviewWeKnora({"doc-1": missing}))
    assert entries2[0]["agent_ready"] is False

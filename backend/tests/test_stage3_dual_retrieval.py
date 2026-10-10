"""Stage-3 dual-retrieval contract tests (p2-194): the Review reads BOTH the
WeKnora surfaces AND the AgentMemory wiki surface, and a writable decision
survives only when every surface that could hold a duplicate answered.
"""

from __future__ import annotations

import os

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from backend.services.hermes_knowledge_workflow import (  # noqa: E402
    _collect_agent_memory_evidence,
    _downgrade_decisions_without_evidence,
)

PACKET = {
    "candidates": [
        {"candidate_id": "k1", "statement": "knowledge statement", "context": "", "evidence_references": []},
        {"candidate_id": "m1", "statement": "memory statement", "context": "", "evidence_references": []},
    ]
}

WRITABLE = [
    {"candidate_id": "k1", "candidate_type": "knowledge", "decision": "new",
     "rationale": "no duplicate", "proposed_content": "body"},
    {"candidate_id": "m1", "candidate_type": "memory", "decision": "supplement",
     "rationale": "extends existing", "proposed_content": "body",
     "target_object": "mem-1", "target_version": "2"},
]


class _AM:
    def __init__(self, *, available=True, fail=False):
        self.available = available
        self.fail = fail
        self.queries = []

    def configured(self) -> bool:
        return self.available

    def search_knowledge(self, query: str) -> dict:
        if self.fail:
            raise RuntimeError("sweep failed midway")
        self.queries.append(query)
        return {
            "wiki_count": 3,
            "searched": 3,
            "hits": [{"wiki_id": "w1", "title": "hit", "snippet": "s", "score": 1}],
        }


# ------------------------------------------------------------ downgrade


def test_writable_decisions_require_the_agent_memory_surface() -> None:
    adjusted, downgraded = _downgrade_decisions_without_evidence(
        WRITABLE,
        knowledge_available=True,
        memory_available=True,
        agent_memory_available=False,
    )
    assert downgraded == ["k1", "m1"]
    assert all(item["decision"] == "human_review" for item in adjusted)
    assert any("AgentMemory evidence unavailable" in item["rationale"] for item in adjusted)
    # the human-review record keeps the evidence trail: the original target
    # is dropped so no write intent survives the downgrade
    assert "target_object" not in adjusted[1]


def test_writable_decisions_survive_when_both_sides_answer() -> None:
    adjusted, downgraded = _downgrade_decisions_without_evidence(
        WRITABLE,
        knowledge_available=True,
        memory_available=True,
        agent_memory_available=True,
    )
    assert downgraded == []
    assert [item["decision"] for item in adjusted] == ["new", "supplement"]


def test_both_sides_unavailable_reports_both_in_the_reason() -> None:
    adjusted, _ = _downgrade_decisions_without_evidence(
        WRITABLE,
        knowledge_available=False,
        memory_available=False,
        agent_memory_available=False,
    )
    assert all("WeKnora and AgentMemory evidence unavailable" in item["rationale"] for item in adjusted)


def test_skill_downgrade_is_independent_of_surfaces() -> None:
    skills = [{"candidate_id": "s1", "candidate_type": "skill", "decision": "new",
               "rationale": "r", "proposed_content": "b"}]
    adjusted, downgraded = _downgrade_decisions_without_evidence(
        skills, knowledge_available=True, memory_available=True,
        agent_memory_available=True,
    )
    assert downgraded == ["s1"]
    assert adjusted[0]["decision"] == "human_review"


# ------------------------------------------------------------ collector


def test_collector_gathers_hits_per_candidate() -> None:
    client = _AM()
    results, available, meta = _collect_agent_memory_evidence(client, PACKET)
    assert available is True
    assert meta == {"wiki_count": 3, "searched": 3}
    assert sorted(results) == ["k1", "m1"]
    assert results["k1"][0]["wiki_id"] == "w1"
    assert client.queries == ["knowledge statement", "memory statement"]


def test_collector_unconfigured_client_is_unavailable() -> None:
    results, available, meta = _collect_agent_memory_evidence(None, PACKET)
    assert available is False
    assert results == {"k1": [], "m1": []}
    assert meta["reason"] == "agent_memory_client_not_configured"


def test_collector_mid_sweep_failure_fails_the_whole_surface() -> None:
    client = _AM(fail=True)
    results, available, meta = _collect_agent_memory_evidence(client, PACKET)
    assert available is False
    assert results == {"k1": [], "m1": []}
    assert "agent_memory_search_failed" in meta["reason"]

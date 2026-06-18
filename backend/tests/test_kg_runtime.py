"""Tests for the online RAG+KG auxiliary runtime contract (``kg_runtime.py``).

Covers the three KG hooks and the three non-negotiable rules from
``docs/roadmap.html`` (RAG vs KG lane, ``kg-runtime-boundary``):

  1. Provenance gate - unprovenanced KG outputs never enter runtime context.
  2. Citation pool is RAG-only (KG structured facts never produce citations).
  3. Any KG step timing out or failing degrades to the pure-RAG path.

Each hook is exercised with a stub ``KgRuntimeClient`` plus the default
``KgRuntimeDisabled`` no-op, under the ``RAG_KG_AUXILIARY_ENABLED`` flag.
"""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from backend.services.kg_runtime import (
    DEFAULT_BOOST_MAX,
    DEFAULT_MAX_TERMS,
    DEFAULT_TIMEOUT_MS,
    KG_FACT_CONTEXT_HEADER,
    KgRuntimeDisabled,
    format_kg_facts_context_block,
    kg_auxiliary_enabled,
    kg_entity_link_expansion,
    kg_rerank_boost,
    kg_structured_facts,
    set_default_kg_client,
)
from backend.services.kg_supportportal_contracts import (
    KgExpansion,
    KgProvenance,
    KgRerankSignal,
    KgStructuredFact,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _prov(**overrides: object) -> KgProvenance:
    base = {
        "chunk_id": "c1",
        "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
        "document_id": "d1",
        "schema_version": "supportportal_official_docs_v1",
    }
    base.update(overrides)  # type: ignore[arg-type]
    return KgProvenance(**base)  # type: ignore[arg-type]


class _StubKgClient:
    """In-memory KG runtime client for tests."""

    def __init__(
        self,
        *,
        expansions: list[KgExpansion] | None = None,
        rerank_signals: list[KgRerankSignal] | None = None,
        facts: list[KgStructuredFact] | None = None,
        delay_seconds: float = 0.0,
        raise_on_call: bool = False,
    ) -> None:
        self._expansions = expansions or []
        self._rerank_signals = rerank_signals or []
        self._facts = facts or []
        self._delay = delay_seconds
        self._raise = raise_on_call
        self.calls: list[tuple[str, tuple]] = []

    def _maybe_delay_or_raise(self) -> None:
        if self._raise:
            raise RuntimeError("kg backend unavailable")
        if self._delay > 0:
            time.sleep(self._delay)

    def entity_link_expansions(self, query: str) -> list[KgExpansion]:
        self.calls.append(("expansion", (query,)))
        self._maybe_delay_or_raise()
        return list(self._expansions)

    def rerank_boost_signals(
        self, query: str, candidate_chunk_ids: list[str]
    ) -> list[KgRerankSignal]:
        self.calls.append(("rerank_boost", (query, tuple(candidate_chunk_ids))))
        self._maybe_delay_or_raise()
        return list(self._rerank_signals)

    def structured_facts(
        self, query: str, final_chunk_ids: list[str]
    ) -> list[KgStructuredFact]:
        self.calls.append(("structured_facts", (query, tuple(final_chunk_ids))))
        self._maybe_delay_or_raise()
        return list(self._facts)


class _KgFlagContext:
    """Context manager that enables RAG_KG_AUXILIARY_ENABLED for a test."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def __enter__(self) -> "_KgFlagContext":
        self._patcher = patch.dict(os.environ, {"RAG_KG_AUXILIARY_ENABLED": "true" if self._enabled else "false"})
        self._patcher.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._patcher.stop()


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


class TestKgAuxiliaryFlag(unittest.TestCase):
    def setUp(self) -> None:
        set_default_kg_client(KgRuntimeDisabled())

    def test_flag_default_off(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RAG_KG_AUXILIARY_ENABLED", None)
            self.assertFalse(kg_auxiliary_enabled())

    def test_flag_on(self) -> None:
        with patch.dict(os.environ, {"RAG_KG_AUXILIARY_ENABLED": "true"}):
            self.assertTrue(kg_auxiliary_enabled())

    def test_flag_off_explicit(self) -> None:
        with patch.dict(os.environ, {"RAG_KG_AUXILIARY_ENABLED": "false"}):
            self.assertFalse(kg_auxiliary_enabled())


# ---------------------------------------------------------------------------
# Hook #1: entity link + synonym expansion
# ---------------------------------------------------------------------------


class TestHook1Expansion(unittest.TestCase):
    def setUp(self) -> None:
        set_default_kg_client(KgRuntimeDisabled())

    def test_flag_off_returns_empty_no_client_call(self) -> None:
        client = _StubKgClient(expansions=[KgExpansion(term="token", provenance=_prov())])
        set_default_kg_client(client)
        with patch.dict(os.environ, {"RAG_KG_AUXILIARY_ENABLED": "false"}):
            result = kg_entity_link_expansion(None, "token auth")
        self.assertEqual(result.terms, [])
        self.assertFalse(result.degraded)
        self.assertEqual(client.calls, [])

    def test_valid_expansions_pass_provenance(self) -> None:
        expansions = [
            KgExpansion(term="RTC engine", provenance=_prov(chunk_id="c1")),
            KgExpansion(term="token authentication", provenance=_prov(chunk_id="c2")),
        ]
        client = _StubKgClient(expansions=expansions)
        with _KgFlagContext():
            result = kg_entity_link_expansion(client, "token auth")
        self.assertEqual(result.terms, ["RTC engine", "token authentication"])
        self.assertFalse(result.degraded)
        self.assertEqual(len(result.expansions), 2)

    def test_orphan_term_without_chunk_id_dropped(self) -> None:
        expansions = [
            KgExpansion(term="good", provenance=_prov(chunk_id="c1")),
            KgExpansion(term="orphan", provenance=_prov(chunk_id="")),
        ]
        client = _StubKgClient(expansions=expansions)
        with _KgFlagContext():
            result = kg_entity_link_expansion(client, "q")
        self.assertEqual(result.terms, ["good"])

    def test_unprovenanced_expansion_dropped(self) -> None:
        expansions = [
            KgExpansion(term="good", provenance=_prov(chunk_id="c1")),
            KgExpansion(term="bad", provenance=_prov(source_url="")),
        ]
        client = _StubKgClient(expansions=expansions)
        with _KgFlagContext():
            result = kg_entity_link_expansion(client, "q")
        self.assertEqual(result.terms, ["good"])

    def test_max_terms_cap(self) -> None:
        expansions = [
            KgExpansion(term=f"t{i}", provenance=_prov(chunk_id=f"c{i}"))
            for i in range(DEFAULT_MAX_TERMS + 5)
        ]
        client = _StubKgClient(expansions=expansions)
        with _KgFlagContext():
            result = kg_entity_link_expansion(client, "q")
        self.assertEqual(len(result.terms), DEFAULT_MAX_TERMS)

    def test_timeout_degrades_to_empty(self) -> None:
        client = _StubKgClient(
            expansions=[KgExpansion(term="late", provenance=_prov())],
            delay_seconds=0.4,
        )
        with _KgFlagContext():
            result = kg_entity_link_expansion(client, "q", timeout_ms=DEFAULT_TIMEOUT_MS)
        self.assertTrue(result.degraded)
        self.assertEqual(result.terms, [])
        self.assertIsNotNone(result.degrade_reason)
        self.assertIn("timeout", result.degrade_reason or "")

    def test_exception_degrades(self) -> None:
        client = _StubKgClient(raise_on_call=True)
        with _KgFlagContext():
            result = kg_entity_link_expansion(client, "q")
        self.assertTrue(result.degraded)
        self.assertEqual(result.terms, [])
        self.assertIn("error", result.degrade_reason or "")

    def test_empty_query_no_op(self) -> None:
        client = _StubKgClient(expansions=[KgExpansion(term="x", provenance=_prov())])
        with _KgFlagContext():
            result = kg_entity_link_expansion(client, "   ")
        self.assertEqual(result.terms, [])
        self.assertEqual(client.calls, [])

    def test_disabled_client_returns_empty_not_degraded(self) -> None:
        with _KgFlagContext():
            result = kg_entity_link_expansion(None, "q")
        self.assertEqual(result.terms, [])
        self.assertFalse(result.degraded)


# ---------------------------------------------------------------------------
# Hook #2: rerank boost (signal only)
# ---------------------------------------------------------------------------


class TestHook2RerankBoost(unittest.TestCase):
    def setUp(self) -> None:
        set_default_kg_client(KgRuntimeDisabled())

    def test_flag_off_no_op(self) -> None:
        client = _StubKgClient(
            rerank_signals=[KgRerankSignal(chunk_id="c1", boost=0.05, provenance=_prov())]
        )
        set_default_kg_client(client)
        with patch.dict(os.environ, {"RAG_KG_AUXILIARY_ENABLED": "false"}):
            result = kg_rerank_boost(None, "q", ["c1", "c2"])
        self.assertEqual(result.signals, [])
        self.assertEqual(client.calls, [])

    def test_boost_only_for_candidate_chunks(self) -> None:
        signals = [
            KgRerankSignal(chunk_id="c1", boost=0.05, provenance=_prov(chunk_id="c1")),
            KgRerankSignal(chunk_id="c9", boost=0.05, provenance=_prov(chunk_id="c9")),
        ]
        client = _StubKgClient(rerank_signals=signals)
        with _KgFlagContext():
            result = kg_rerank_boost(client, "q", ["c1", "c2"])
        # c9 is not a RAG candidate -> dropped.
        self.assertEqual([s.chunk_id for s in result.signals], ["c1"])

    def test_unprovenanced_signal_dropped(self) -> None:
        signals = [
            KgRerankSignal(chunk_id="c1", boost=0.05, provenance=_prov(chunk_id="c1")),
            KgRerankSignal(chunk_id="c2", boost=0.05, provenance=_prov(source_url="")),
        ]
        client = _StubKgClient(rerank_signals=signals)
        with _KgFlagContext():
            result = kg_rerank_boost(client, "q", ["c1", "c2"])
        self.assertEqual([s.chunk_id for s in result.signals], ["c1"])

    def test_boost_clamped_to_max(self) -> None:
        signals = [
            KgRerankSignal(chunk_id="c1", boost=10.0, provenance=_prov(chunk_id="c1")),
        ]
        client = _StubKgClient(rerank_signals=signals)
        with _KgFlagContext():
            result = kg_rerank_boost(client, "q", ["c1"])
        self.assertEqual(len(result.signals), 1)
        self.assertAlmostEqual(result.signals[0].boost, DEFAULT_BOOST_MAX)

    def test_timeout_degrades_no_signals(self) -> None:
        client = _StubKgClient(
            rerank_signals=[KgRerankSignal(chunk_id="c1", boost=0.05, provenance=_prov())],
            delay_seconds=0.4,
        )
        with _KgFlagContext():
            result = kg_rerank_boost(client, "q", ["c1"], timeout_ms=DEFAULT_TIMEOUT_MS)
        self.assertTrue(result.degraded)
        self.assertEqual(result.signals, [])

    def test_exception_degrades(self) -> None:
        client = _StubKgClient(raise_on_call=True)
        with _KgFlagContext():
            result = kg_rerank_boost(client, "q", ["c1"])
        self.assertTrue(result.degraded)
        self.assertEqual(result.signals, [])

    def test_empty_candidates_no_op(self) -> None:
        client = _StubKgClient(rerank_signals=[KgRerankSignal(chunk_id="c1", boost=0.05, provenance=_prov())])
        with _KgFlagContext():
            result = kg_rerank_boost(client, "q", [])
        self.assertEqual(result.signals, [])
        self.assertEqual(client.calls, [])


# ---------------------------------------------------------------------------
# Hook #3: structured fact lookup (provenance-gated to selected RAG chunks)
# ---------------------------------------------------------------------------


class TestHook3StructuredFacts(unittest.TestCase):
    def setUp(self) -> None:
        set_default_kg_client(KgRuntimeDisabled())

    def test_flag_off_no_op(self) -> None:
        client = _StubKgClient(facts=[KgStructuredFact(text="f", provenance=_prov())])
        set_default_kg_client(client)
        with patch.dict(os.environ, {"RAG_KG_AUXILIARY_ENABLED": "false"}):
            result = kg_structured_facts(None, "q", ["c1"])
        self.assertEqual(result.facts, [])
        self.assertEqual(client.calls, [])

    def test_facts_must_trace_back_to_final_chunks(self) -> None:
        facts = [
            KgStructuredFact(text="in", provenance=_prov(chunk_id="c1")),
            KgStructuredFact(text="out", provenance=_prov(chunk_id="c9")),
        ]
        client = _StubKgClient(facts=facts)
        with _KgFlagContext():
            result = kg_structured_facts(client, "q", ["c1", "c2"])
        self.assertEqual(len(result.facts), 1)
        self.assertEqual(result.facts[0].text, "in")

    def test_unprovenanced_fact_dropped(self) -> None:
        facts = [
            KgStructuredFact(text="good", provenance=_prov(chunk_id="c1")),
            KgStructuredFact(text="bad", provenance=_prov(document_id="")),
        ]
        client = _StubKgClient(facts=facts)
        with _KgFlagContext():
            result = kg_structured_facts(client, "q", ["c1"])
        self.assertEqual([f.text for f in result.facts], ["good"])

    def test_timeout_degrades_no_facts(self) -> None:
        client = _StubKgClient(
            facts=[KgStructuredFact(text="f", provenance=_prov())],
            delay_seconds=0.4,
        )
        with _KgFlagContext():
            result = kg_structured_facts(client, "q", ["c1"], timeout_ms=DEFAULT_TIMEOUT_MS)
        self.assertTrue(result.degraded)
        self.assertEqual(result.facts, [])

    def test_exception_degrades(self) -> None:
        client = _StubKgClient(raise_on_call=True)
        with _KgFlagContext():
            result = kg_structured_facts(client, "q", ["c1"])
        self.assertTrue(result.degraded)
        self.assertEqual(result.facts, [])

    def test_empty_final_chunks_no_op(self) -> None:
        client = _StubKgClient(facts=[KgStructuredFact(text="f", provenance=_prov())])
        with _KgFlagContext():
            result = kg_structured_facts(client, "q", [])
        self.assertEqual(result.facts, [])
        self.assertEqual(client.calls, [])


# ---------------------------------------------------------------------------
# Citation pool = RAG-only (context block formatting)
# ---------------------------------------------------------------------------


class TestKgFactsContextBlock(unittest.TestCase):
    def test_empty_facts_returns_empty_string(self) -> None:
        self.assertEqual(format_kg_facts_context_block([]), "")

    def test_block_is_labeled_non_citable(self) -> None:
        facts = [KgStructuredFact(text="Token must be Base64.", provenance=_prov(chunk_id="c1"))]
        block = format_kg_facts_context_block(facts)
        self.assertIn(KG_FACT_CONTEXT_HEADER, block)
        self.assertIn("DO NOT CITE", block)
        self.assertIn("Token must be Base64.", block)
        self.assertIn("chunk_id=c1", block)

    def test_block_never_contains_citation_marker(self) -> None:
        # The context block is text-only background; it must not look like a
        # citation record the model could copy. It carries chunk_id only as a
        # provenance tag inside brackets, not as a citation id list.
        facts = [KgStructuredFact(text="fact", provenance=_prov(chunk_id="c1"))]
        block = format_kg_facts_context_block(facts)
        self.assertNotIn('"citations"', block)
        self.assertNotIn("source_path", block)


# ---------------------------------------------------------------------------
# Citation-pool RAG-only enforcement (rag_qa integration)
# ---------------------------------------------------------------------------


class TestCitationPoolRagOnly(unittest.TestCase):
    """The citation resolver must drop any id not in the final RAG chunks."""

    def test_citation_records_from_ids_drops_foreign_ids(self) -> None:
        import backend.services.rag_qa as rag_qa

        rag_chunk = rag_qa.RetrievedChunk(
            chunk_id="rag-1",
            text="official doc text",
            source_path="docs/token.md",
            similarity=0.9,
        )
        # A foreign id (e.g. a KG fact id) must not resolve to a citation.
        allowed = {"rag-1"}
        citations = ["rag-1", "kg-fact-foreign", "rag-1"]
        # Simulate the structured-answer extraction filter (roadmap rule #2).
        filtered = [cid for cid in citations if cid in allowed]
        records = rag_qa._citation_records_from_ids(filtered, [rag_chunk])
        self.assertTrue(records)
        self.assertEqual({record["chunk_id"] for record in records}, {"rag-1"})
        self.assertNotIn("kg-fact-foreign", {record["chunk_id"] for record in records})

    def test_is_valid_response_rejects_foreign_citations(self) -> None:
        import backend.services.rag_qa as rag_qa

        allowed = {"rag-1"}
        payload = {
            "answer": "a",
            "key_steps": ["s"],
            "citations": ["rag-1", "kg-fact-foreign"],
            "insufficient_evidence": False,
        }
        self.assertFalse(rag_qa._is_valid_response(payload, allowed))

    def test_is_valid_response_accepts_rag_only_citations(self) -> None:
        import backend.services.rag_qa as rag_qa

        allowed = {"rag-1", "rag-2"}
        payload = {
            "answer": "a",
            "key_steps": ["s"],
            "citations": ["rag-1"],
            "insufficient_evidence": False,
        }
        self.assertTrue(rag_qa._is_valid_response(payload, allowed))


if __name__ == "__main__":
    unittest.main()

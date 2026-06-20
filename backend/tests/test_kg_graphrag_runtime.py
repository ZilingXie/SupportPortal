"""Tests for the live RAG+KG runtime client (``kg_graphrag_runtime.py``).

Covers three layers, none of which require a live Neo4j:

  1. ``GraphRagKgRuntimeClient`` - the pure mapping from KG search hits to the
     three hook contract types, including the provenance gate and the chunk-id
     scoping rules (KG never introduces a chunk RAG did not surface).
  2. ``GraphitiSearchBackend`` edge -> ``GraphFactRecord`` assembly, with the
     graphiti-coupled resolution methods stubbed (the live graphiti calls are
     the deliberately-isolated integration glue).
  3. Factory gating: ``build_graphrag_kg_runtime_client`` /
     ``maybe_install_default_kg_client`` stay inert when the flag is off or no
     Neo4j backend is configured, and the client is wired end-to-end through
     the real ``kg_runtime`` hooks.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.kg_graphrag_runtime import (
    DEFAULT_SEARCH_NUM_RESULTS,
    GROUP_ID,
    GraphFactRecord,
    GraphitiSearchBackend,
    GraphRagKgRuntimeClient,
    _graphrag_config_from_env,
    _provenance_from_episode,
    build_graphrag_kg_runtime_client,
    maybe_install_default_kg_client,
)
from backend.services.kg_runtime import (
    KgRuntimeClient,
    KgRuntimeDisabled,
    get_default_kg_client,
    kg_entity_link_expansion,
    kg_rerank_boost,
    kg_rerank_boost_max,
    kg_structured_facts,
    set_default_kg_client,
)
from backend.services.kg_supportportal_contracts import (
    KgProvenance,
    has_valid_provenance,
)


def _prov(chunk_id: str = "c1") -> KgProvenance:
    return KgProvenance(
        chunk_id=chunk_id,
        source_url="https://docs.agora.io/en/video-calling/token-authentication",
        document_id="d1",
        schema_version="supportportal_official_docs_v1",
    )


class _FakeBackend:
    """In-memory ``GraphSearchBackend`` returning canned records."""

    def __init__(self, records: list[GraphFactRecord]) -> None:
        self._records = records
        self.calls: list[tuple[str, int]] = []

    def search_facts(self, query: str, *, num_results: int) -> list[GraphFactRecord]:
        self.calls.append((query, num_results))
        return list(self._records)


# ---------------------------------------------------------------------------
# Pure mapping client
# ---------------------------------------------------------------------------


class TestExpansionMapping(unittest.TestCase):
    def test_entity_terms_become_expansions_with_provenance(self) -> None:
        backend = _FakeBackend(
            [
                GraphFactRecord(
                    fact="RTC engine is the Agora engine",
                    relation="alias_of",
                    entity_terms=("RTC engine", "Agora engine"),
                    provenance=_prov(),
                )
            ]
        )
        client = GraphRagKgRuntimeClient(backend)
        terms = [exp.term for exp in client.entity_link_expansions("video call")]
        self.assertEqual(terms, ["RTC engine", "Agora engine"])
        self.assertTrue(all(has_valid_provenance(exp) for exp in client.entity_link_expansions("video call")))

    def test_records_without_provenance_are_dropped(self) -> None:
        backend = _FakeBackend(
            [GraphFactRecord(fact="x", entity_terms=("Token",), provenance=None)]
        )
        client = GraphRagKgRuntimeClient(backend)
        self.assertEqual(client.entity_link_expansions("token"), [])

    def test_terms_in_query_and_duplicates_excluded(self) -> None:
        backend = _FakeBackend(
            [
                GraphFactRecord(entity_terms=("token", "Token", "channel"), fact="f", provenance=_prov()),
                GraphFactRecord(entity_terms=("channel",), fact="f2", provenance=_prov("c2")),
            ]
        )
        client = GraphRagKgRuntimeClient(backend)
        # "token" is in the query (case-insensitive) and "channel" repeats.
        terms = [exp.term for exp in client.entity_link_expansions("how to get a token")]
        self.assertEqual(terms, ["channel"])

    def test_blank_query_short_circuits(self) -> None:
        backend = _FakeBackend([GraphFactRecord(entity_terms=("a",), fact="f", provenance=_prov())])
        client = GraphRagKgRuntimeClient(backend)
        self.assertEqual(client.entity_link_expansions("   "), [])
        self.assertEqual(backend.calls, [])


class TestRerankMapping(unittest.TestCase):
    def test_only_candidate_chunks_boosted(self) -> None:
        backend = _FakeBackend(
            [
                GraphFactRecord(fact="f1", provenance=_prov("c1")),
                GraphFactRecord(fact="f2", provenance=_prov("c2")),
                GraphFactRecord(fact="f3", provenance=_prov("c3")),
            ]
        )
        client = GraphRagKgRuntimeClient(backend)
        signals = client.rerank_boost_signals("q", ["c1", "c3"])
        self.assertEqual({s.chunk_id for s in signals}, {"c1", "c3"})

    def test_boost_within_max_and_top_ranked_highest(self) -> None:
        backend = _FakeBackend(
            [
                GraphFactRecord(fact="top", provenance=_prov("c1")),
                GraphFactRecord(fact="low", provenance=_prov("c2")),
            ]
        )
        client = GraphRagKgRuntimeClient(backend, boost_max=0.05)
        signals = {s.chunk_id: s.boost for s in client.rerank_boost_signals("q", ["c1", "c2"])}
        self.assertLessEqual(max(signals.values()), 0.05)
        self.assertGreaterEqual(min(signals.values()), 0.0)
        self.assertGreater(signals["c1"], signals["c2"])

    def test_duplicate_chunk_keeps_max_boost(self) -> None:
        backend = _FakeBackend(
            [
                GraphFactRecord(fact="top", provenance=_prov("c1")),
                GraphFactRecord(fact="dup-lower-rank", provenance=_prov("c1")),
            ]
        )
        client = GraphRagKgRuntimeClient(backend, boost_max=0.05)
        signals = client.rerank_boost_signals("q", ["c1"])
        self.assertEqual(len(signals), 1)
        self.assertAlmostEqual(signals[0].boost, 0.05)

    def test_empty_candidate_set_short_circuits(self) -> None:
        backend = _FakeBackend([GraphFactRecord(fact="f", provenance=_prov("c1"))])
        client = GraphRagKgRuntimeClient(backend)
        self.assertEqual(client.rerank_boost_signals("q", []), [])
        self.assertEqual(backend.calls, [])

    def test_default_boost_max_reads_env_cap(self) -> None:
        backend = _FakeBackend([GraphFactRecord(fact="f", provenance=_prov("c1"))])
        client = GraphRagKgRuntimeClient(backend)  # no explicit boost_max
        signals = client.rerank_boost_signals("q", ["c1"])
        self.assertLessEqual(signals[0].boost, kg_rerank_boost_max())


class TestStructuredFactMapping(unittest.TestCase):
    def test_only_final_chunk_facts_returned(self) -> None:
        backend = _FakeBackend(
            [
                GraphFactRecord(fact="keep", provenance=_prov("c1")),
                GraphFactRecord(fact="drop", provenance=_prov("c9")),
            ]
        )
        client = GraphRagKgRuntimeClient(backend)
        facts = client.structured_facts("q", ["c1"])
        self.assertEqual([f.text for f in facts], ["keep"])
        self.assertTrue(has_valid_provenance(facts[0]))

    def test_blank_fact_and_duplicates_dropped(self) -> None:
        backend = _FakeBackend(
            [
                GraphFactRecord(fact="  ", provenance=_prov("c1")),
                GraphFactRecord(fact="Same fact", provenance=_prov("c1")),
                GraphFactRecord(fact="same fact", provenance=_prov("c1")),
            ]
        )
        client = GraphRagKgRuntimeClient(backend)
        facts = client.structured_facts("q", ["c1"])
        self.assertEqual([f.text for f in facts], ["Same fact"])

    def test_empty_final_set_short_circuits(self) -> None:
        backend = _FakeBackend([GraphFactRecord(fact="f", provenance=_prov("c1"))])
        client = GraphRagKgRuntimeClient(backend)
        self.assertEqual(client.structured_facts("q", []), [])
        self.assertEqual(backend.calls, [])


class TestProtocolConformance(unittest.TestCase):
    def test_client_satisfies_runtime_protocol(self) -> None:
        client = GraphRagKgRuntimeClient(_FakeBackend([]))
        self.assertIsInstance(client, KgRuntimeClient)


# ---------------------------------------------------------------------------
# Provenance reconstruction
# ---------------------------------------------------------------------------


class _FakeEpisode:
    def __init__(self, metadata: dict | None) -> None:
        self.episode_metadata = metadata


class TestProvenanceFromEpisode(unittest.TestCase):
    def test_complete_metadata_builds_provenance(self) -> None:
        episode = _FakeEpisode(
            {
                "supportportal_chunk_id": "c1",
                "supportportal_source_url": "https://docs.agora.io/x",
                "supportportal_document_id": "d1",
                "supportportal_schema_version": "supportportal_official_docs_v1",
            }
        )
        prov = _provenance_from_episode(episode)
        self.assertIsNotNone(prov)
        self.assertEqual(prov.chunk_id, "c1")

    def test_missing_field_returns_none(self) -> None:
        episode = _FakeEpisode(
            {
                "supportportal_chunk_id": "c1",
                "supportportal_source_url": "https://docs.agora.io/x",
                # document_id missing
                "supportportal_schema_version": "v1",
            }
        )
        self.assertIsNone(_provenance_from_episode(episode))

    def test_no_metadata_returns_none(self) -> None:
        self.assertIsNone(_provenance_from_episode(_FakeEpisode(None)))


# ---------------------------------------------------------------------------
# GraphitiSearchBackend edge -> record assembly (graphiti calls stubbed)
# ---------------------------------------------------------------------------


class _FakeEdge:
    def __init__(self, *, fact, name, episodes, source=None, target=None) -> None:
        self.fact = fact
        self.name = name
        self.episodes = episodes
        self.source_node_uuid = source
        self.target_node_uuid = target


class _FakeGraphiti:
    def __init__(self, edges: list[_FakeEdge]) -> None:
        self._edges = edges
        self.driver = object()
        self.search_kwargs: dict | None = None

    async def search(self, *, query, num_results, group_ids):
        self.search_kwargs = {"query": query, "num_results": num_results, "group_ids": group_ids}
        return self._edges


class _FakeGraphRag:
    def __init__(self, edges: list[_FakeEdge]) -> None:
        self.graphiti = _FakeGraphiti(edges)


class _StubBackend(GraphitiSearchBackend):
    def __init__(self, graph_rag, prov_map, name_map) -> None:
        super().__init__(graph_rag)
        self._prov_map = prov_map
        self._name_map = name_map

    async def _resolve_provenance(self, graphiti, episode_uuids):
        return {u: self._prov_map[u] for u in episode_uuids if u in self._prov_map}

    async def _resolve_node_names(self, graphiti, node_uuids):
        return {u: self._name_map[u] for u in node_uuids if u in self._name_map}


class TestGraphitiSearchBackendAssembly(unittest.TestCase):
    def test_edges_become_records_with_provenance_and_terms(self) -> None:
        edges = [
            _FakeEdge(
                fact="Token authenticates the user",
                name="authenticates",
                episodes=["ep1"],
                source="n1",
                target="n2",
            )
        ]
        graph_rag = _FakeGraphRag(edges)
        backend = _StubBackend(
            graph_rag,
            prov_map={"ep1": _prov("c1")},
            name_map={"n1": "Token", "n2": "User"},
        )
        records = backend.search_facts("how does token work", num_results=5)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.fact, "Token authenticates the user")
        self.assertEqual(rec.relation, "authenticates")
        self.assertEqual(rec.entity_terms, ("Token", "User"))
        self.assertEqual(rec.provenance.chunk_id, "c1")
        # Search is scoped to the official-docs partition.
        self.assertEqual(graph_rag.graphiti.search_kwargs["group_ids"], [GROUP_ID])

    def test_edge_without_resolvable_provenance_yields_none(self) -> None:
        edges = [_FakeEdge(fact="orphan", name="rel", episodes=["epX"], source="n1")]
        backend = _StubBackend(_FakeGraphRag(edges), prov_map={}, name_map={})
        records = backend.search_facts("q", num_results=5)
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].provenance)
        self.assertEqual(records[0].entity_terms, ())


# ---------------------------------------------------------------------------
# Factory gating + end-to-end through the real hooks
# ---------------------------------------------------------------------------


_NEO4J_ENV_KEYS = (
    "KG_NEO4J_URI",
    "KG_NEO4J_USER",
    "KG_NEO4J_PASSWORD",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
)

_KG_MODEL_ENV_KEYS = (
    "KG_LLM_API_KEY",
    "KG_LLM_BASE_URL",
    "KG_LLM_MODEL",
    "KG_EMBEDDING_API_KEY",
    "KG_EMBEDDING_MODEL",
    "KG_EMBEDDING_BASE_URL",
    "KG_EMBEDDING_DIM",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_FALLBACK_MODEL",
    "SILICONFLOW_API_KEY",
    "SILLICONFLOW_KEY",
    "SILICONFLOW_BASE_URL",
    "EMBEDDING_MODEL_ID",
    "SILICONFLOW_EMBEDDING_DIMENSIONS",
)


class TestFactoryGating(unittest.TestCase):
    def tearDown(self) -> None:
        set_default_kg_client(KgRuntimeDisabled())

    def test_build_returns_none_without_neo4j_env(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in _NEO4J_ENV_KEYS:
                os.environ.pop(key, None)
            self.assertIsNone(build_graphrag_kg_runtime_client())

    def test_install_noop_when_flag_off(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RAG_KG_AUXILIARY_ENABLED", None)
            self.assertFalse(maybe_install_default_kg_client())
            self.assertIsInstance(get_default_kg_client(), KgRuntimeDisabled)

    def test_install_noop_when_flag_on_but_no_backend(self) -> None:
        env = {"RAG_KG_AUXILIARY_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=False):
            for key in _NEO4J_ENV_KEYS:
                os.environ.pop(key, None)
            self.assertFalse(maybe_install_default_kg_client())
            self.assertIsInstance(get_default_kg_client(), KgRuntimeDisabled)

    def test_graphrag_config_from_env_falls_back_to_existing_model_envs(self) -> None:
        env = {
            "KG_NEO4J_URI": "bolt://local_neo4j:7687",
            "KG_NEO4J_USER": "neo4j",
            "KG_NEO4J_PASSWORD": "supportportal-kg-local",
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_FALLBACK_MODEL": "deepseek-chat",
            "SILICONFLOW_API_KEY": "siliconflow-secret",
            "SILICONFLOW_BASE_URL": "https://api.siliconflow.cn/v1",
            "EMBEDDING_MODEL_ID": "BAAI/bge-m3",
            "SILICONFLOW_EMBEDDING_DIMENSIONS": "1024",
        }
        with patch.dict(os.environ, env, clear=False):
            for key in _KG_MODEL_ENV_KEYS:
                if key not in env:
                    os.environ.pop(key, None)

            config = _graphrag_config_from_env()

        self.assertIsNotNone(config)
        self.assertEqual(config.llm_api_key, "deepseek-secret")
        self.assertEqual(config.llm_base_url, "https://api.deepseek.com/v1")
        self.assertEqual(config.llm_model, "deepseek-chat")
        self.assertEqual(config.embedding_api_key, "siliconflow-secret")
        self.assertEqual(config.embedding_model, "BAAI/bge-m3")
        self.assertEqual(config.embedding_base_url, "https://api.siliconflow.cn/v1")
        self.assertEqual(config.embedding_dim, 1024)

    def test_kg_specific_env_overrides_shared_model_envs(self) -> None:
        env = {
            "KG_NEO4J_URI": "bolt://local_neo4j:7687",
            "KG_NEO4J_USER": "neo4j",
            "KG_NEO4J_PASSWORD": "supportportal-kg-local",
            "KG_LLM_API_KEY": "kg-llm-secret",
            "KG_LLM_BASE_URL": "https://kg-llm.example/v1",
            "KG_LLM_MODEL": "kg-chat",
            "KG_EMBEDDING_API_KEY": "kg-embedding-secret",
            "KG_EMBEDDING_MODEL": "kg-embedding",
            "KG_EMBEDDING_BASE_URL": "https://kg-embedding.example/v1",
            "KG_EMBEDDING_DIM": "768",
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "SILICONFLOW_API_KEY": "siliconflow-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            config = _graphrag_config_from_env()

        self.assertIsNotNone(config)
        self.assertEqual(config.llm_api_key, "kg-llm-secret")
        self.assertEqual(config.llm_base_url, "https://kg-llm.example/v1")
        self.assertEqual(config.llm_model, "kg-chat")
        self.assertEqual(config.embedding_api_key, "kg-embedding-secret")
        self.assertEqual(config.embedding_model, "kg-embedding")
        self.assertEqual(config.embedding_base_url, "https://kg-embedding.example/v1")
        self.assertEqual(config.embedding_dim, 768)


class TestEndToEndThroughHooks(unittest.TestCase):
    def tearDown(self) -> None:
        set_default_kg_client(KgRuntimeDisabled())

    def test_installed_client_drives_all_three_hooks(self) -> None:
        backend = _FakeBackend(
            [
                GraphFactRecord(
                    fact="Token authenticates the user",
                    relation="authenticates",
                    entity_terms=("RTC engine",),
                    provenance=_prov("c1"),
                )
            ]
        )
        set_default_kg_client(GraphRagKgRuntimeClient(backend))
        with patch.dict(os.environ, {"RAG_KG_AUXILIARY_ENABLED": "true"}):
            expansion = kg_entity_link_expansion(None, "how does a session work")
            boost = kg_rerank_boost(None, "how does a session work", ["c1", "c2"])
            facts = kg_structured_facts(None, "how does a session work", ["c1"])
        self.assertIn("RTC engine", expansion.terms)
        self.assertEqual({s.chunk_id for s in boost.signals}, {"c1"})
        self.assertEqual([f.text for f in facts.facts], ["Token authenticates the user"])

    def test_default_search_num_results_constant(self) -> None:
        self.assertEqual(DEFAULT_SEARCH_NUM_RESULTS, 10)


if __name__ == "__main__":
    unittest.main()

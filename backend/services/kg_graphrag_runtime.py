"""Live RAG+KG runtime client backed by the vendored cusmem GraphRAG.

Wires ``KgRuntimeClient`` (the three online hooks in ``kg_runtime.py``) to the
vendored Graphiti/GraphRAG knowledge graph built by the offline ingest path
(``kg_offline_ingest.py`` / ``kg_graphrag_adapter.py``).

The module is split so the valuable mapping logic stays unit-testable without a
live Neo4j:

  - ``GraphFactRecord`` - a small, backend-agnostic view of one KG search hit
    (fact text, relation, entity terms, resolved provenance, score).
  - ``GraphRagKgRuntimeClient`` - PURE mapping from ``GraphFactRecord`` to the
    three contract types (``KgExpansion`` / ``KgRerankSignal`` /
    ``KgStructuredFact``). No graph I/O here, so it is fully testable with a
    fake backend.
  - ``GraphitiSearchBackend`` - the ONLY place that touches vendored graphiti
    (async hybrid search + episode/entity hydration, wrapped sync). Isolated so
    the mapping above stays verifiable.
  - Factory + ``maybe_install_default_kg_client()`` - construct from env and,
    when ``RAG_KG_AUXILIARY_ENABLED`` is on and a graph backend is reachable,
    register the client via ``set_default_kg_client()``. Any failure leaves the
    default ``KgRuntimeDisabled`` no-op in place (degrade to pure RAG).

Provenance rule (roadmap rule #1): every mapped output carries a
``KgProvenance`` rebuilt from the episode's ``supportportal_*`` metadata;
records without full provenance are dropped here AND re-validated by the hooks
(defense in depth). KG never introduces a chunk the RAG chain did not surface:
rerank/fact outputs are scoped to the caller-supplied chunk-id sets.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from backend.services.kg_runtime import (
    KgRuntimeClient,
    kg_auxiliary_enabled,
    kg_rerank_boost_max,
    set_default_kg_client,
)
from backend.services.kg_supportportal_contracts import (
    KgExpansion,
    KgProvenance,
    KgRerankSignal,
    KgStructuredFact,
)

LOGGER = logging.getLogger(__name__)

# Graph partition written by the offline ingest adapter (see
# ``kg_graphrag_adapter.build_episode_payload`` -> ``group_id``). Runtime search
# is scoped to this partition so KG only ever reads official-doc derived facts.
GROUP_ID = "supportportal_official_docs"

# How many KG facts to pull per hook call. The hooks apply their own caps
# (e.g. ``KG_EXPANSION_MAX_TERMS``) on top of this, so a small fan-out is fine.
DEFAULT_SEARCH_NUM_RESULTS = 10

# ---------------------------------------------------------------------------
# Env config (only consumed by the live backend factory)
# ---------------------------------------------------------------------------

KG_NEO4J_URI_ENV = "KG_NEO4J_URI"
KG_NEO4J_USER_ENV = "KG_NEO4J_USER"
KG_NEO4J_PASSWORD_ENV = "KG_NEO4J_PASSWORD"
KG_SEARCH_NUM_RESULTS_ENV = "KG_SEARCH_NUM_RESULTS"
KG_LLM_API_KEY_ENV = "KG_LLM_API_KEY"
KG_LLM_BASE_URL_ENV = "KG_LLM_BASE_URL"
KG_LLM_MODEL_ENV = "KG_LLM_MODEL"
KG_EMBEDDING_API_KEY_ENV = "KG_EMBEDDING_API_KEY"
KG_EMBEDDING_MODEL_ENV = "KG_EMBEDDING_MODEL"
KG_EMBEDDING_BASE_URL_ENV = "KG_EMBEDDING_BASE_URL"
KG_EMBEDDING_DIM_ENV = "KG_EMBEDDING_DIM"


# ---------------------------------------------------------------------------
# Backend-agnostic search record + backend protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphFactRecord:
    """One KG search hit, decoupled from any vendored graphiti type.

    ``provenance`` is ``None`` when the hit cannot be traced back to a
    fully-provenanced official-doc chunk; such records are dropped by every
    mapping method below.
    """

    fact: str
    relation: str | None = None
    entity_terms: tuple[str, ...] = field(default_factory=tuple)
    provenance: KgProvenance | None = None
    score: float | None = None


@runtime_checkable
class GraphSearchBackend(Protocol):
    """Minimal read-only search surface the runtime client depends on."""

    def search_facts(self, query: str, *, num_results: int) -> list[GraphFactRecord]:
        """Return KG facts relevant to ``query`` with resolved provenance."""


# ---------------------------------------------------------------------------
# Pure mapping client (no graph I/O - fully unit-testable)
# ---------------------------------------------------------------------------


def _query_token_set(query: str) -> set[str]:
    return {tok for tok in (query or "").lower().split() if tok}


class GraphRagKgRuntimeClient:
    """``KgRuntimeClient`` implementation mapping KG search hits to the hooks.

    All graph access is delegated to ``backend``; this class only transforms
    ``GraphFactRecord`` lists into provenance-carrying contract objects and
    enforces the chunk-id scoping rules. The online hooks re-validate provenance
    and re-clamp boosts, so this class stays the single mapping concern.
    """

    def __init__(
        self,
        backend: GraphSearchBackend,
        *,
        num_results: int | None = None,
        boost_max: float | None = None,
    ) -> None:
        self._backend = backend
        self._num_results = num_results if (num_results and num_results > 0) else DEFAULT_SEARCH_NUM_RESULTS
        # ``None`` means "read the env-configured cap lazily at call time".
        self._boost_max = boost_max

    def _search(self, query: str) -> list[GraphFactRecord]:
        if not str(query or "").strip():
            return []
        records = self._backend.search_facts(query, num_results=self._num_results)
        return [rec for rec in (records or []) if isinstance(rec, GraphFactRecord)]

    # -- Hook #1: entity link + synonym expansion --------------------------
    def entity_link_expansions(self, query: str) -> list[KgExpansion]:
        query_tokens = _query_token_set(query)
        seen: set[str] = set()
        out: list[KgExpansion] = []
        for rec in self._search(query):
            if rec.provenance is None:
                continue
            for term in rec.entity_terms:
                normalized = str(term or "").strip()
                key = normalized.lower()
                if not normalized or key in seen or key in query_tokens:
                    continue
                seen.add(key)
                out.append(
                    KgExpansion(
                        term=normalized,
                        provenance=rec.provenance,
                        relation=rec.relation,
                    )
                )
        return out

    # -- Hook #2: rerank boost (signal only, scoped to RAG candidates) ------
    def rerank_boost_signals(
        self, query: str, candidate_chunk_ids: list[str]
    ) -> list[KgRerankSignal]:
        candidate_set = {str(cid) for cid in candidate_chunk_ids or [] if str(cid).strip()}
        if not candidate_set:
            return []
        boost_max = self._boost_max if self._boost_max is not None else kg_rerank_boost_max()
        records = self._search(query)
        total = len(records)
        best_by_chunk: dict[str, KgRerankSignal] = {}
        for idx, rec in enumerate(records):
            if rec.provenance is None:
                continue
            chunk_id = str(rec.provenance.chunk_id)
            if chunk_id not in candidate_set:
                # KG can only boost chunks the RAG chain already surfaced.
                continue
            # Graded by search rank: top hit -> boost_max, decreasing. The hook
            # re-clamps to [0, KG_RERANK_BOOST_MAX], so this only needs to be
            # monotonic and within range.
            boost = boost_max * (1.0 - idx / total) if total > 1 else boost_max
            existing = best_by_chunk.get(chunk_id)
            if existing is None or boost > existing.boost:
                best_by_chunk[chunk_id] = KgRerankSignal(
                    chunk_id=chunk_id,
                    boost=boost,
                    provenance=rec.provenance,
                    reason=rec.relation,
                )
        return list(best_by_chunk.values())

    # -- Hook #3: structured fact lookup (scoped to selected RAG chunks) ----
    def structured_facts(
        self, query: str, final_chunk_ids: list[str]
    ) -> list[KgStructuredFact]:
        final_set = {str(cid) for cid in final_chunk_ids or [] if str(cid).strip()}
        if not final_set:
            return []
        seen: set[tuple[str, str]] = set()
        out: list[KgStructuredFact] = []
        for rec in self._search(query):
            if rec.provenance is None:
                continue
            chunk_id = str(rec.provenance.chunk_id)
            if chunk_id not in final_set:
                # A fact must trace back to a chunk RAG actually selected.
                continue
            text = str(rec.fact or "").strip()
            if not text:
                continue
            dedupe_key = (chunk_id, text.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(
                KgStructuredFact(
                    text=text,
                    provenance=rec.provenance,
                    relation=rec.relation,
                )
            )
        return out


# ---------------------------------------------------------------------------
# Live graphiti backend (the only vendored-graphiti coupling)
# ---------------------------------------------------------------------------


def _ensure_vendor_cusmem_path() -> None:
    vendor_root = Path(__file__).resolve().parents[2] / "vendor" / "cusmem"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))


def _provenance_from_episode(episode: Any) -> KgProvenance | None:
    """Rebuild ``KgProvenance`` from an episode's ``supportportal_*`` metadata.

    Returns ``None`` unless all four provenance fields are present and non-empty.
    """
    meta = getattr(episode, "episode_metadata", None) or {}
    chunk_id = meta.get("supportportal_chunk_id")
    source_url = meta.get("supportportal_source_url")
    document_id = meta.get("supportportal_document_id")
    schema_version = meta.get("supportportal_schema_version")
    fields = (chunk_id, source_url, document_id, schema_version)
    if not all(isinstance(value, str) and value.strip() for value in fields):
        return None
    return KgProvenance(
        chunk_id=chunk_id,
        source_url=source_url,
        document_id=document_id,
        schema_version=schema_version,
    )


class GraphitiSearchBackend:
    """``GraphSearchBackend`` over a vendored ``GraphRAG`` instance.

    Runs graphiti's async hybrid search and hydrates each edge with provenance
    (from its source episodes) and entity terms (from its connected nodes). All
    async work is driven via ``asyncio.run`` because the hooks call backends
    synchronously inside a bounded thread-pool worker.
    """

    def __init__(self, graph_rag: Any, *, group_id: str = GROUP_ID) -> None:
        self._graph_rag = graph_rag
        self._group_id = group_id

    def search_facts(self, query: str, *, num_results: int) -> list[GraphFactRecord]:
        import asyncio

        return asyncio.run(self._search_async(query, num_results))

    async def _search_async(self, query: str, num_results: int) -> list[GraphFactRecord]:
        graphiti = self._graph_rag.graphiti
        edges = await graphiti.search(
            query=query,
            num_results=num_results,
            group_ids=[self._group_id],
        )

        episode_uuids: set[str] = set()
        node_uuids: set[str] = set()
        for edge in edges or []:
            episode_uuids.update(getattr(edge, "episodes", None) or [])
            for attr in ("source_node_uuid", "target_node_uuid"):
                uuid = getattr(edge, attr, None)
                if uuid:
                    node_uuids.add(uuid)

        provenance_by_episode = await self._resolve_provenance(graphiti, episode_uuids)
        name_by_node = await self._resolve_node_names(graphiti, node_uuids)

        records: list[GraphFactRecord] = []
        for edge in edges or []:
            provenance = None
            for episode_uuid in getattr(edge, "episodes", None) or []:
                provenance = provenance_by_episode.get(episode_uuid)
                if provenance is not None:
                    break
            terms: list[str] = []
            for attr in ("source_node_uuid", "target_node_uuid"):
                name = name_by_node.get(getattr(edge, attr, None))
                if name:
                    terms.append(name)
            records.append(
                GraphFactRecord(
                    fact=str(getattr(edge, "fact", "") or ""),
                    relation=getattr(edge, "name", None),
                    entity_terms=tuple(terms),
                    provenance=provenance,
                )
            )
        return records

    async def _resolve_provenance(
        self, graphiti: Any, episode_uuids: set[str]
    ) -> dict[str, KgProvenance]:
        if not episode_uuids:
            return {}
        from graphiti_core.nodes import EpisodicNode

        episodes = await EpisodicNode.get_by_uuids(graphiti.driver, list(episode_uuids))
        resolved: dict[str, KgProvenance] = {}
        for episode in episodes or []:
            provenance = _provenance_from_episode(episode)
            if provenance is not None:
                resolved[getattr(episode, "uuid", "")] = provenance
        resolved.pop("", None)
        return resolved

    async def _resolve_node_names(
        self, graphiti: Any, node_uuids: set[str]
    ) -> dict[str, str]:
        if not node_uuids:
            return {}
        try:
            from graphiti_core.nodes import EntityNode

            nodes = await EntityNode.get_by_uuids(graphiti.driver, list(node_uuids))
        except Exception as exc:  # entity-name hydration is best-effort
            LOGGER.debug("KG entity-name resolution skipped (%s)", exc)
            return {}
        return {
            getattr(node, "uuid", ""): getattr(node, "name", "")
            for node in nodes or []
            if getattr(node, "uuid", None) and getattr(node, "name", None)
        }


# ---------------------------------------------------------------------------
# Factory + default-client installation
# ---------------------------------------------------------------------------


def _search_num_results_from_env() -> int:
    raw = (os.getenv(KG_SEARCH_NUM_RESULTS_ENV) or "").strip()
    if not raw:
        return DEFAULT_SEARCH_NUM_RESULTS
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_SEARCH_NUM_RESULTS
    return parsed if parsed > 0 else DEFAULT_SEARCH_NUM_RESULTS


def _graphrag_config_from_env() -> Any | None:
    """Build a vendored ``Config`` from env, or ``None`` if Neo4j is unset.

    Neo4j connection details are required (no sensible default for a real
    graph); LLM/embedding knobs fall back to the vendored ``Config`` defaults
    when unset.
    """
    uri = os.getenv(KG_NEO4J_URI_ENV) or os.getenv("NEO4J_URI")
    user = os.getenv(KG_NEO4J_USER_ENV) or os.getenv("NEO4J_USER")
    password = os.getenv(KG_NEO4J_PASSWORD_ENV) or os.getenv("NEO4J_PASSWORD")
    if not (uri and user and password):
        return None

    _ensure_vendor_cusmem_path()
    from graphiti_rag.config import Config

    def _first_env(*names: str) -> str | None:
        for name in names:
            value = os.getenv(name)
            if value:
                return value
        return None

    def _llm_base_url_from_env() -> str | None:
        explicit = os.getenv(KG_LLM_BASE_URL_ENV) or os.getenv("OPENAI_BASE_URL")
        if explicit:
            return explicit
        deepseek_base = (os.getenv("DEEPSEEK_BASE_URL") or "").rstrip("/")
        if deepseek_base:
            return deepseek_base if deepseek_base.endswith("/v1") else f"{deepseek_base}/v1"
        return None

    overrides: dict[str, Any] = {
        "neo4j_uri": uri,
        "neo4j_user": user,
        "neo4j_password": password,
    }
    optional = {
        "llm_api_key": _first_env(KG_LLM_API_KEY_ENV, "DEEPSEEK_API_KEY", "OPENAI_API_KEY"),
        "llm_base_url": _llm_base_url_from_env(),
        "llm_model": _first_env(KG_LLM_MODEL_ENV, "DEEPSEEK_FALLBACK_MODEL", "OPENAI_CHAT_MODEL"),
        "embedding_api_key": _first_env(
            KG_EMBEDDING_API_KEY_ENV,
            "SILICONFLOW_API_KEY",
            "SILLICONFLOW_KEY",
            "OPENAI_API_KEY",
        ),
        "embedding_model": _first_env(KG_EMBEDDING_MODEL_ENV, "EMBEDDING_MODEL_ID"),
        "embedding_base_url": _first_env(KG_EMBEDDING_BASE_URL_ENV, "SILICONFLOW_BASE_URL"),
    }
    overrides.update({key: value for key, value in optional.items() if value})

    dim_raw = (
        os.getenv(KG_EMBEDDING_DIM_ENV)
        or os.getenv("SILICONFLOW_EMBEDDING_DIMENSIONS")
        or ""
    ).strip()
    if dim_raw:
        try:
            overrides["embedding_dim"] = int(dim_raw)
        except ValueError:
            LOGGER.warning("Invalid %s=%r; using Config default.", KG_EMBEDDING_DIM_ENV, dim_raw)

    return Config(**overrides)


def build_graphrag() -> Any | None:
    """Construct a vendored ``GraphRAG`` from env, or ``None`` on missing config.

    Construction is connection-lazy (the Neo4j driver connects on first query),
    so a missing graph only surfaces later as a per-call timeout/failure that
    the hooks degrade on. Any construction error degrades to ``None``.
    """
    config = _graphrag_config_from_env()
    if config is None:
        LOGGER.info(
            "KG GraphRAG backend not configured (missing Neo4j env); staying on pure-RAG."
        )
        return None
    try:
        _ensure_vendor_cusmem_path()
        from graphiti_rag.graph_rag import GraphRAG

        return GraphRAG(config)
    except Exception as exc:
        LOGGER.warning(
            "KG GraphRAG backend construction failed (%s); staying on pure-RAG.", exc
        )
        return None


def build_graphrag_kg_runtime_client() -> KgRuntimeClient | None:
    """Build a live ``GraphRagKgRuntimeClient`` or ``None`` if unavailable."""
    graph_rag = build_graphrag()
    if graph_rag is None:
        return None
    return GraphRagKgRuntimeClient(
        GraphitiSearchBackend(graph_rag),
        num_results=_search_num_results_from_env(),
    )


def maybe_install_default_kg_client() -> bool:
    """Install the live KG client as the process default when enabled.

    Returns ``True`` only when ``RAG_KG_AUXILIARY_ENABLED`` is on AND a graph
    backend was constructed. Otherwise the default ``KgRuntimeDisabled`` no-op
    stays in place and the runtime remains on the pure-RAG chain.
    """
    if not kg_auxiliary_enabled():
        LOGGER.info("RAG_KG_AUXILIARY_ENABLED is off; KG runtime client not installed.")
        return False
    client = build_graphrag_kg_runtime_client()
    if client is None:
        LOGGER.info(
            "RAG_KG_AUXILIARY_ENABLED is on but no KG backend available; "
            "KG runtime client not installed (pure-RAG)."
        )
        return False
    set_default_kg_client(client)
    LOGGER.info("Installed live GraphRAG KG runtime client (RAG+KG auxiliary active).")
    return True

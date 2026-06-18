"""Online RAG+KG auxiliary runtime contract.

Implements the three KG hooks fixed by `docs/roadmap.html` (RAG vs KG lane,
`kg-runtime-boundary`) for the Client AI online RAG path:

  - Hook #1: entity link + synonym expansion (hard ~150ms cap, degrade on timeout)
  - Hook #2: rerank boost - signal only, never truncates RAG candidates
  - Hook #3: structured fact lookup - facts must trace back to a RAG chunk_id

Three non-negotiable rules (see roadmap note):

  1. Provenance gate - any KG entity/edge without ``chunk_id`` + ``source_url``
     is rejected from runtime context. Reuses ``has_valid_provenance`` from
     ``kg_supportportal_contracts`` as the single enforcement point.
  2. KG cannot independently answer; the citation pool only contains RAG chunks.
     KG structured facts enrich generation context but never enter the citation
     pool.
  3. Any KG step timing out or failing degrades to the pure-RAG path. Each hook
     runs under a hard timeout (default 150ms) and returns an empty/degraded
     result on timeout, exception, or provenance failure.

The RAG retrieval chain (vector + BM25 + FTS + RRF + metadata prune + external
rerank) is intentionally NOT touched here; the hooks only consume RAG outputs
and never add or remove RAG candidates.

The whole path is gated by ``RAG_KG_AUXILIARY_ENABLED`` (default ``false``) so
default runtime behavior remains on the pure-RAG chain.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from backend.services.kg_supportportal_contracts import (
    KgExpansion,
    KgRerankSignal,
    KgStructuredFact,
    has_valid_provenance,
)

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (env-driven, mirroring rag_qa feature-flag style)
# ---------------------------------------------------------------------------

KG_AUXILIARY_FLAG = "RAG_KG_AUXILIARY_ENABLED"
KG_EXPANSION_TIMEOUT_ENV = "KG_EXPANSION_TIMEOUT_MS"
KG_RERANK_BOOST_TIMEOUT_ENV = "KG_RERANK_BOOST_TIMEOUT_MS"
KG_FACT_LOOKUP_TIMEOUT_ENV = "KG_FACT_LOOKUP_TIMEOUT_MS"
KG_EXPANSION_MAX_TERMS_ENV = "KG_EXPANSION_MAX_TERMS"
KG_RERANK_BOOST_MAX_ENV = "KG_RERANK_BOOST_MAX"

DEFAULT_TIMEOUT_MS = 150
DEFAULT_MAX_TERMS = 8
DEFAULT_BOOST_MAX = 0.05


def kg_auxiliary_enabled() -> bool:
    raw = (os.getenv(KG_AUXILIARY_FLAG) or "").strip().lower()
    if not raw:
        return False
    return raw in {"1", "true", "yes", "on"}


def _positive_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _positive_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def kg_expansion_timeout_ms() -> int:
    return _positive_int_env(KG_EXPANSION_TIMEOUT_ENV, DEFAULT_TIMEOUT_MS)


def kg_rerank_boost_timeout_ms() -> int:
    return _positive_int_env(KG_RERANK_BOOST_TIMEOUT_ENV, DEFAULT_TIMEOUT_MS)


def kg_fact_lookup_timeout_ms() -> int:
    return _positive_int_env(KG_FACT_LOOKUP_TIMEOUT_ENV, DEFAULT_TIMEOUT_MS)


def kg_expansion_max_terms() -> int:
    return _positive_int_env(KG_EXPANSION_MAX_TERMS_ENV, DEFAULT_MAX_TERMS)


def kg_rerank_boost_max() -> float:
    return _positive_float_env(KG_RERANK_BOOST_MAX_ENV, DEFAULT_BOOST_MAX)


# ---------------------------------------------------------------------------
# Runtime client protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class KgRuntimeClient(Protocol):
    """Minimal read-only interface a KG backend must implement for the hooks.

    Each method MUST return only objects that already carry provenance; the
    hooks still re-validate provenance at the boundary so a misbehaving backend
    cannot leak unprovenanced data into runtime context.
    """

    def entity_link_expansions(self, query: str) -> list[KgExpansion]:
        """Return synonym/alias expansion terms for the query."""

    def rerank_boost_signals(
        self, query: str, candidate_chunk_ids: list[str]
    ) -> list[KgRerankSignal]:
        """Return additive boost signals for candidate chunks."""

    def structured_facts(
        self, query: str, final_chunk_ids: list[str]
    ) -> list[KgStructuredFact]:
        """Return structured facts traced back to the final RAG chunks."""


class KgRuntimeDisabled:
    """Default no-op client used when no KG backend is configured.

    Every hook call against this client returns empty results, which the hooks
    treat as "KG contributed nothing" (not a degradation) so the pure-RAG path
    is unaffected.
    """

    def entity_link_expansions(self, query: str) -> list[KgExpansion]:
        return []

    def rerank_boost_signals(
        self, query: str, candidate_chunk_ids: list[str]
    ) -> list[KgRerankSignal]:
        return []

    def structured_facts(
        self, query: str, final_chunk_ids: list[str]
    ) -> list[KgStructuredFact]:
        return []


_DEFAULT_CLIENT: KgRuntimeClient = KgRuntimeDisabled()

# A single shared executor for KG hook calls. Hooks are synchronous best-effort
# reads; a tiny pool is sufficient because each call is bounded by a hard
# timeout and the hooks are sequential within one RAG run.
_HOOK_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kg-hook")


def set_default_kg_client(client: KgRuntimeClient | None) -> None:
    """Override the process-wide default KG runtime client (used by tests)."""
    global _DEFAULT_CLIENT
    _DEFAULT_CLIENT = client if client is not None else KgRuntimeDisabled()


def get_default_kg_client() -> KgRuntimeClient:
    return _DEFAULT_CLIENT


# ---------------------------------------------------------------------------
# Hook result envelopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KgExpansionResult:
    """Result envelope for hook #1."""

    terms: list[str] = field(default_factory=list)
    expansions: list[KgExpansion] = field(default_factory=list)
    degraded: bool = False
    degrade_reason: str | None = None
    latency_ms: float = 0.0


@dataclass(frozen=True)
class KgRerankBoostResult:
    """Result envelope for hook #2."""

    signals: list[KgRerankSignal] = field(default_factory=list)
    degraded: bool = False
    degrade_reason: str | None = None
    latency_ms: float = 0.0


@dataclass(frozen=True)
class KgStructuredFactsResult:
    """Result envelope for hook #3."""

    facts: list[KgStructuredFact] = field(default_factory=list)
    degraded: bool = False
    degrade_reason: str | None = None
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _provenance_chunk_id(obj: Any) -> str | None:
    provenance = getattr(obj, "provenance", None)
    if provenance is None:
        return None
    return getattr(provenance, "chunk_id", None)


def _run_with_timeout(fn: Any, *, timeout_ms: int, stage: str) -> tuple[Any, float, str | None]:
    """Run ``fn`` under a hard timeout. Returns (result, latency_ms, degrade_reason).

    On timeout/exception returns ``(None, latency_ms, reason)``. The caller
    decides what an empty result means.
    """
    import time

    started_at = time.perf_counter()
    future = _HOOK_EXECUTOR.submit(fn)
    try:
        result = future.result(timeout=max(0.001, timeout_ms) / 1000.0)
    except FutureTimeoutError:
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        future.cancel()
        LOGGER.warning("KG %s timed out after %sms (degraded)", stage, timeout_ms)
        return None, latency_ms, f"timeout_after_{timeout_ms}ms"
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        LOGGER.warning("KG %s failed (degraded): %s", stage, exc)
        return None, latency_ms, f"error:{type(exc).__name__}"
    latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
    return result, latency_ms, None


# ---------------------------------------------------------------------------
# Hook #1: entity link + synonym expansion
# ---------------------------------------------------------------------------


def kg_entity_link_expansion(
    client: KgRuntimeClient | None,
    query: str,
    *,
    timeout_ms: int | None = None,
    max_terms: int | None = None,
) -> KgExpansionResult:
    """Hook #1 - KG entity link + synonym expansion for query understanding.

    Returns validated expansion terms (each ``KgExpansion`` must pass the
    provenance gate). On timeout/exception/provenance failure returns an empty
    result with ``degraded=True`` so the caller falls back to the pure-RAG
    expansion path (callers leave expansions untouched on degradation).
    """
    if not kg_auxiliary_enabled():
        return KgExpansionResult()
    if not str(query or "").strip():
        return KgExpansionResult()

    runtime_client = client if client is not None else get_default_kg_client()
    cap_ms = timeout_ms if timeout_ms is not None else kg_expansion_timeout_ms()
    term_cap = max_terms if max_terms is not None else kg_expansion_max_terms()

    raw, latency_ms, reason = _run_with_timeout(
        lambda: runtime_client.entity_link_expansions(query),
        timeout_ms=cap_ms,
        stage="expansion",
    )
    if reason is not None:
        return KgExpansionResult(degraded=True, degrade_reason=reason, latency_ms=latency_ms)

    validated: list[KgExpansion] = []
    seen_terms: set[str] = set()
    for item in raw or []:
        if not isinstance(item, KgExpansion):
            continue
        if not has_valid_provenance(item):
            # Provenance gate: unprovenanced expansion terms never enter context.
            continue
        term = str(item.term or "").strip()
        if not term or term.lower() in seen_terms:
            continue
        seen_terms.add(term.lower())
        validated.append(item)
        if len(validated) >= term_cap:
            break

    return KgExpansionResult(
        terms=[item.term for item in validated],
        expansions=validated,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Hook #2: rerank boost (signal only)
# ---------------------------------------------------------------------------


def kg_rerank_boost(
    client: KgRuntimeClient | None,
    query: str,
    candidate_chunk_ids: list[str],
    *,
    timeout_ms: int | None = None,
) -> KgRerankBoostResult:
    """Hook #2 - KG rerank boost over already-reranked RAG candidates.

    Constraints (roadmap rule):
      - KG can only boost RAG candidates; any signal whose ``chunk_id`` is not
        in ``candidate_chunk_ids`` is dropped (KG never introduces new chunks).
      - Boost is additive and clamped to ``[0, KG_RERANK_BOOST_MAX]``.
      - This is a re-sort signal only; it does not truncate or remove chunks.
      - On timeout/exception/provenance failure returns ``degraded=True`` with
        no signals, so the caller keeps the RAG reranked order unchanged.
    """
    if not kg_auxiliary_enabled():
        return KgRerankBoostResult()
    candidate_set = {str(cid) for cid in candidate_chunk_ids or [] if str(cid).strip()}
    if not str(query or "").strip() or not candidate_set:
        return KgRerankBoostResult()

    runtime_client = client if client is not None else get_default_kg_client()
    cap_ms = timeout_ms if timeout_ms is not None else kg_rerank_boost_timeout_ms()
    boost_max = kg_rerank_boost_max()

    raw, latency_ms, reason = _run_with_timeout(
        lambda: runtime_client.rerank_boost_signals(query, list(candidate_set)),
        timeout_ms=cap_ms,
        stage="rerank_boost",
    )
    if reason is not None:
        return KgRerankBoostResult(degraded=True, degrade_reason=reason, latency_ms=latency_ms)

    validated: list[KgRerankSignal] = []
    for signal in raw or []:
        if not isinstance(signal, KgRerankSignal):
            continue
        if not has_valid_provenance(signal):
            continue
        if str(signal.chunk_id) not in candidate_set:
            # KG can only boost existing RAG candidates.
            continue
        clamped_boost = max(0.0, min(boost_max, float(signal.boost)))
        if clamped_boost == float(signal.boost):
            validated.append(signal)
        else:
            validated.append(
                KgRerankSignal(
                    chunk_id=signal.chunk_id,
                    boost=clamped_boost,
                    provenance=signal.provenance,
                    reason=signal.reason,
                )
            )

    return KgRerankBoostResult(signals=validated, latency_ms=latency_ms)


# ---------------------------------------------------------------------------
# Hook #3: structured fact lookup (provenance-gated to selected RAG chunks)
# ---------------------------------------------------------------------------


def kg_structured_facts(
    client: KgRuntimeClient | None,
    query: str,
    final_chunk_ids: list[str],
    *,
    timeout_ms: int | None = None,
) -> KgStructuredFactsResult:
    """Hook #3 - KG structured fact lookup for generation context.

    Constraints (roadmap rule):
      - Each fact must pass the provenance gate AND its ``provenance.chunk_id``
        must be one of the selected RAG ``final_chunk_ids`` (a fact must trace
        back to a chunk the RAG chain actually surfaced).
      - Facts are context-only and NEVER enter the citation pool (enforced by
        the caller passing them in a separate non-citable context block).
      - On timeout/exception/provenance failure returns ``degraded=True`` with
        no facts, so generation proceeds with pure-RAG context.
    """
    if not kg_auxiliary_enabled():
        return KgStructuredFactsResult()
    final_set = {str(cid) for cid in final_chunk_ids or [] if str(cid).strip()}
    if not str(query or "").strip() or not final_set:
        return KgStructuredFactsResult()

    runtime_client = client if client is not None else get_default_kg_client()
    cap_ms = timeout_ms if timeout_ms is not None else kg_fact_lookup_timeout_ms()

    raw, latency_ms, reason = _run_with_timeout(
        lambda: runtime_client.structured_facts(query, list(final_set)),
        timeout_ms=cap_ms,
        stage="structured_facts",
    )
    if reason is not None:
        return KgStructuredFactsResult(degraded=True, degrade_reason=reason, latency_ms=latency_ms)

    validated: list[KgStructuredFact] = []
    for fact in raw or []:
        if not isinstance(fact, KgStructuredFact):
            continue
        if not has_valid_provenance(fact):
            continue
        fact_chunk_id = _provenance_chunk_id(fact)
        if fact_chunk_id is None or str(fact_chunk_id) not in final_set:
            # Fact must trace back to a selected RAG chunk.
            continue
        validated.append(fact)

    return KgStructuredFactsResult(facts=validated, latency_ms=latency_ms)


# ---------------------------------------------------------------------------
# Generation-context formatting helper (non-citable block)
# ---------------------------------------------------------------------------

KG_FACT_CONTEXT_HEADER = "Supplementary structured facts (KG-derived, context-only - DO NOT CITE)"


def format_kg_facts_context_block(facts: list[KgStructuredFact]) -> str:
    """Render validated KG facts as a non-citable context block.

    The block is deliberately labeled so the answer model treats it as
    background context and never cites it. Citations remain RAG-only because
    this block is appended to the prompt context separately from
    ``final_chunks`` and never enters the citation id pool.
    """
    if not facts:
        return ""
    lines = [KG_FACT_CONTEXT_HEADER]
    for fact in facts:
        provenance = fact.provenance
        source = f" [chunk_id={provenance.chunk_id}]"
        lines.append(f"- {fact.text}{source}")
    return "\n".join(lines)

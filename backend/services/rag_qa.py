from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from backend.services.embedding_provider import (
    DEFAULT_PGVECTOR_TABLE,
    embedding_model_id,
    embedding_provider_name,
    get_embedding_provider,
)
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    ModelProfile,
    QUERY_EXPANSION_SCENARIO,
    RAG_AGENT_PLANNER_SCENARIO,
    RAG_ANSWER_SCENARIO,
    RAG_CONTEXT_COMPRESSION_SCENARIO,
    resolve_model_profile,
)
from backend.services.rag_context_budget import (
    PackedEvidence,
    build_packed_evidence,
    estimate_text_tokens,
    model_context_window,
)
from backend.services.prompts.rag_agent_planner import (
    build_rag_agent_planner_system_prompt,
    build_rag_agent_planner_user_prompt,
)
from backend.services.prompts.rag_answer import build_rag_answer_system_prompt, build_rag_answer_user_prompt
from backend.services.query_understanding import (
    QueryUnderstandingResult,
    RetrievalPlan,
    build_prf_expansions,
    downpush_hard_filters,
    understand_rag_query,
)
from backend.services.rag_tokenizer import is_bm25_query_stopword, tokenize_bm25_query
from backend.services.support_products import (
    build_support_product_prompt_scope,
    build_support_product_rag_role,
)

logger = logging.getLogger(__name__)
_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "are",
    "be",
    "by",
    "can",
    "do",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "where",
    "which",
    "with",
    "you",
    "your",
}

INSUFFICIENT_EVIDENCE_REPLY = (
    "I couldn't find enough information in the available support knowledge base to answer that question."
)

AGENT_PLAN_VERSION = "v1"
_RUNTIME_CAPABILITY_UNAVAILABLE_UNTIL: dict[str, float] = {}
_RUNTIME_QUOTA_COOLDOWN_SECONDS = 600.0
_RUNTIME_NETWORK_COOLDOWN_SECONDS = 120.0
_ACTIVE_VECTOR_TABLE_CACHE_TTL_SECONDS = 60.0
_ACTIVE_VECTOR_TABLE_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_ACTIVE_VECTOR_TABLE_CACHE_LOCK = threading.Lock()
_LIGHT_PATH_BM25_CANDIDATE_K = 12
_LIGHT_PATH_FTS_CANDIDATE_K = 12
_LIGHT_PATH_FUSION_CANDIDATE_K = 12
_LIGHT_PATH_RERANK_TOP_N = 8
_LIGHT_PATH_CONTEXT_CHUNK_LIMIT = 3
_LIGHT_PATH_FAST_ANSWER_MODEL = "gpt-5.4-mini"
_LIGHT_PATH_FAST_ANSWER_REASONING_EFFORT = "low"


def _build_answer_system_prompt(product: str | None = None) -> str:
    return build_rag_answer_system_prompt(
        insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
        product_role=build_support_product_rag_role(product),
        product_scope=build_support_product_prompt_scope(product),
    )


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    source_path: str
    similarity: float
    index_role: str = "primary"
    doc_id: str | None = None
    h1: str | None = None
    h2: str | None = None
    h3: str | None = None
    source_url: str | None = None
    source_type: str | None = None
    chunk_strategy: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    rerank_score: float | None = None
    rerank_reasons: list[str] = field(default_factory=list)
    retrieval_sources: list[str] = field(default_factory=list)
    candidate_trace: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetadataHints:
    language: str | None = None
    method_name: str | None = None
    intent_terms: tuple[str, ...] = ()
    technical_terms: tuple[str, ...] = ()


@dataclass
class RagAnswer:
    answer: str
    confidence: float
    sources: list[str]
    citations: list[dict[str, str]]


@dataclass
class RagQueryTrace:
    query_type: str
    retrieval_strategy: str
    vector_candidates_count: int
    bm25_candidates_count: int
    reranked_candidates_count: int
    retrieved_chunk_ids: list[str]
    selected_chunk_ids: list[str]
    vector_retrieval_latency_ms: float
    bm25_retrieval_latency_ms: float
    retrieval_latency_ms: float
    rerank_latency_ms: float
    generation_latency_ms: float
    total_latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    embedding_tokens: int
    embedding_provider: str | None
    embedding_model: str | None
    embedding_dimensions: int | None
    embedding_request_meta: list[dict[str, Any]]
    model_name: str | None
    answer_length: int
    citation_count: int
    cited_chunk_ids: list[str]
    needs_human: bool
    handoff_reason: str | None
    confidence_score: float
    primary_source_type: str | None
    primary_chunk_strategy: str | None
    reranker_provider: str | None = None
    reranker_model: str | None = None
    generation_mode: str = "structured_answer"
    structured_retry_used: bool = False
    extractive_fallback_used: bool = False
    selected_doc_count: int = 0
    top1_similarity_score: float | None = None
    avg_selected_similarity_score: float | None = None
    citation_coverage_ratio: float | None = None
    retrieval_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_contexts: list[dict[str, Any]] = field(default_factory=list)
    metadata_hints: dict[str, Any] = field(default_factory=dict)
    metadata_filter_applied: bool = False
    metadata_filter_type: str | None = None
    error_flag: bool = False
    timeout_flag: bool = False
    error_type: str | None = None
    intent_latency_ms: float = 0.0
    rewrite_latency_ms: float = 0.0
    query_understanding_enabled: bool = False
    query_understanding_version: str | None = None
    query_profile: str | None = None
    glossary_version: str | None = None
    self_query_version: str | None = None
    fallback_mode: str | None = None
    glossary_hit_terms: list[str] = field(default_factory=list)
    applied_hard_filters: dict[str, str] = field(default_factory=dict)
    applied_soft_signals: dict[str, list[str]] = field(default_factory=dict)
    rewritten_queries: list[str] = field(default_factory=list)
    decomposition_subqueries: list[str] = field(default_factory=list)
    dictionary_hits: list[dict[str, Any]] = field(default_factory=list)
    rule_expansions: list[str] = field(default_factory=list)
    llm_expansions: list[str] = field(default_factory=list)
    prf_expansions: list[str] = field(default_factory=list)
    hard_filter_sources: dict[str, str] = field(default_factory=dict)
    cache_hit: bool = False
    prf_used: bool = False
    query_expansion_enabled: bool = False
    query_expansion_model: str | None = None
    first_pass_candidate_count: int = 0
    second_pass_candidate_count: int = 0
    agent_enabled: bool = False
    agent_plan_version: str | None = None
    query_class: str | None = None
    agent_iterations: list[dict[str, Any]] = field(default_factory=list)
    agent_recovery_action: str | None = None
    ticket_context_used: bool = False
    primary_shadow_mix: dict[str, int] = field(default_factory=dict)
    context_budget_enabled: bool = False
    context_window: int = 0
    reserved_output_tokens: int = 0
    buffer_tokens: int = 0
    raw_context_token_estimate: int = 0
    packed_context_token_estimate: int = 0
    compression_triggered: bool = False
    compression_trigger_reason: str | None = None
    compression_mode: str | None = None
    compression_model: str | None = None
    extractive_segment_count: int = 0
    packed_evidence_count: int = 0
    packed_context_text: str | None = None
    packed_chunk_ids: list[str] = field(default_factory=list)
    query_expansion_usage_ledger: list[dict[str, Any]] = field(default_factory=list)
    context_compression_usage_ledger: list[dict[str, Any]] = field(default_factory=list)
    execution_mode: str = "legacy"
    agent_fallback_used: bool = False
    agent_fallback_reason: str | None = None
    preflight_probe_latency_ms: float = 0.0
    vector_setup_skipped: bool = False
    light_path_used: bool = False
    answer_profile_used: str | None = None
    answer_profile_fallback_used: bool = False


@dataclass
class RagQueryResult:
    answer: RagAnswer
    trace: RagQueryTrace


class RagExecutionCancelled(RuntimeError):
    def __init__(self, stage: str) -> None:
        super().__init__(f"RAG execution cancelled during {stage}")
        self.stage = str(stage or "").strip() or "unknown"


def _raise_if_cancelled(
    stage: str,
    *,
    should_cancel: Callable[[], bool] | None,
    record_stage: Callable[[str], None] | None = None,
) -> None:
    normalized_stage = str(stage or "").strip() or "unknown"
    if callable(record_stage):
        try:
            record_stage(normalized_stage)
        except Exception:
            pass
    if callable(should_cancel) and should_cancel():
        raise RagExecutionCancelled(normalized_stage)


@dataclass(frozen=True)
class AgenticRetrievalPlan:
    query_class: str
    first_pass_tools: list[str]
    query_variants: list[tuple[str, str]]
    decomposition_targets: list[str]
    evidence_goal: str
    recovery_bias: str
    ticket_context_used: bool = False
    exact_terms: list[str] = field(default_factory=list)
    light_path: bool = False


@dataclass(frozen=True)
class AgenticJudgeDecision:
    decision: str
    reason: str
    confidence: float
    recovery_action: str | None = None


@dataclass(frozen=True)
class AgenticIterationTrace:
    round_index: int
    tool_names: list[str]
    query_variants: list[str]
    selected_chunk_ids: list[str]
    decision: str
    recovery_action: str | None = None


@dataclass(frozen=True)
class AgenticRoundResult:
    retrieved_chunks: list[RetrievedChunk]
    reranked_chunks: list[RetrievedChunk]
    final_chunks: list[RetrievedChunk]
    rerank_info: dict[str, Any]
    judge: AgenticJudgeDecision
    iteration_trace: AgenticIterationTrace
    vector_candidate_count: int = 0
    bm25_candidate_count: int = 0
    vector_latency_ms: float = 0.0
    bm25_latency_ms: float = 0.0
    keyword_latency_ms: float = 0.0
    rerank_latency_ms: float = 0.0
    used_seed_tools: list[str] = field(default_factory=list)


def _feature_flag_enabled(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _clean_env_text(name: str) -> str:
    return (os.getenv(name) or "").strip()


def clear_active_vector_table_cache() -> None:
    with _ACTIVE_VECTOR_TABLE_CACHE_LOCK:
        _ACTIVE_VECTOR_TABLE_CACHE.clear()


def _active_vector_table_cache_key(config: dict[str, Any]) -> tuple[str, str] | None:
    configured_dsn = str(config.get("dsn") or "").strip()
    configured_table = str(config.get("table") or "").strip()
    if not configured_dsn or not configured_table:
        return None
    return configured_dsn, configured_table


def _cached_active_vector_table(config: dict[str, Any]) -> str | None:
    cache_key = _active_vector_table_cache_key(config)
    if cache_key is None:
        return None
    now = time.time()
    with _ACTIVE_VECTOR_TABLE_CACHE_LOCK:
        cached = _ACTIVE_VECTOR_TABLE_CACHE.get(cache_key)
        if cached is None:
            return None
        expires_at, table_name = cached
        if expires_at <= now:
            _ACTIVE_VECTOR_TABLE_CACHE.pop(cache_key, None)
            return None
        return table_name


def _store_active_vector_table_cache(config: dict[str, Any], table_name: str) -> None:
    cache_key = _active_vector_table_cache_key(config)
    if cache_key is None:
        return
    with _ACTIVE_VECTOR_TABLE_CACHE_LOCK:
        _ACTIVE_VECTOR_TABLE_CACHE[cache_key] = (
            time.time() + _ACTIVE_VECTOR_TABLE_CACHE_TTL_SECONDS,
            str(table_name or "").strip(),
        )


def _runtime_capability_key(capability: str, provider: str | None = None) -> str:
    normalized_capability = str(capability or "").strip().lower() or "unknown"
    normalized_provider = str(provider or "").strip().lower()
    return f"{normalized_capability}:{normalized_provider}" if normalized_provider else normalized_capability


def _runtime_cooldown_seconds(error: Any) -> float:
    message = str(error or "").strip().lower()
    if any(marker in message for marker in ["401", "403", "429", "insufficient", "balance", "quota"]):
        return _RUNTIME_QUOTA_COOLDOWN_SECONDS
    if any(marker in message for marker in ["timeout", "timed out", "ssl", "temporary failure", "connection reset"]):
        return _RUNTIME_NETWORK_COOLDOWN_SECONDS
    return 0.0


def _runtime_capability_available(capability: str, *, provider: str | None = None) -> bool:
    key = _runtime_capability_key(capability, provider=provider)
    unavailable_until = _RUNTIME_CAPABILITY_UNAVAILABLE_UNTIL.get(key)
    if unavailable_until is None:
        return True
    if time.time() >= float(unavailable_until):
        _RUNTIME_CAPABILITY_UNAVAILABLE_UNTIL.pop(key, None)
        return True
    return False


def _record_runtime_capability_failure(
    capability: str,
    *,
    provider: str | None = None,
    error: Any,
) -> None:
    cooldown_seconds = _runtime_cooldown_seconds(error)
    if cooldown_seconds <= 0:
        return
    key = _runtime_capability_key(capability, provider=provider)
    _RUNTIME_CAPABILITY_UNAVAILABLE_UNTIL[key] = time.time() + cooldown_seconds


def _openai_api_key() -> str:
    return _clean_env_text("OPENAI_API_KEY")


def _siliconflow_api_key() -> str:
    return (
        _clean_env_text("SILICONFLOW_API_KEY")
        or _clean_env_text("SILICONFLOW_KEY")
        or _clean_env_text("SILLICONFLOW_KEY")
        or _clean_env_text("siliconflow_key")
        or _clean_env_text("silliconflow_key")
    )


def _embedding_capability_enabled(provider: str | None = None) -> bool:
    if not _feature_flag_enabled("RAG_VECTOR_RETRIEVAL_ENABLED", True):
        return False
    normalized_provider = str(provider or embedding_provider_name()).strip().lower()
    if normalized_provider == "siliconflow":
        return bool(_siliconflow_api_key())
    if normalized_provider == "openai":
        return bool(_openai_api_key())
    if normalized_provider == "local_bge_m3":
        return True
    return False


def _rerank_capability_enabled(
    *,
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
) -> bool:
    if not _feature_flag_enabled("RAG_RERANK_ENABLED", True):
        return False
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider != "siliconflow":
        return False
    return bool(api_key and base_url and model)


def _is_simple_lexical_query(message: str) -> bool:
    normalized = " ".join(str(message or "").split()).strip()
    if not normalized:
        return False
    if re.search(r"\b\d{3,5}\b", normalized.lower()):
        return False
    return len(normalized.split()) <= 6 and len(_extract_query_terms(normalized, max_terms=8)) <= 4


def _effective_answer_reasoning_effort(
    *,
    base_effort: str | None,
    query_class: str | None = None,
    query_type: str | None = None,
) -> str:
    normalized = str(base_effort or "").strip().lower() or "medium"
    complex_default = _clean_env_text("RAG_COMPLEX_ANSWER_REASONING_EFFORT").lower() or "high"
    if query_class in {"troubleshooting_why", "comparison"} or query_type == "troubleshooting":
        return complex_default
    return normalized


def _copy_chunk(chunk: RetrievedChunk) -> RetrievedChunk:
    return replace(
        chunk,
        metadata=dict(chunk.metadata),
        rerank_reasons=list(chunk.rerank_reasons),
        retrieval_sources=list(chunk.retrieval_sources),
        candidate_trace=dict(chunk.candidate_trace),
    )


def _chunk_dedupe_key(chunk: RetrievedChunk) -> str:
    return chunk.chunk_id or f"{chunk.source_path}:{chunk.text[:120]}"


def _merge_variant_chunks(
    existing: list[RetrievedChunk],
    incoming: list[RetrievedChunk],
    *,
    source_label: str,
    query_variant: str,
    query_kind: str,
) -> list[RetrievedChunk]:
    merged: dict[str, RetrievedChunk] = {_chunk_dedupe_key(chunk): _copy_chunk(chunk) for chunk in existing}
    for item in incoming:
        chunk = _copy_chunk(item)
        dedupe_key = _chunk_dedupe_key(chunk)
        query_trace = {"kind": query_kind, "query": query_variant}
        chunk.retrieval_sources = list(dict.fromkeys([*chunk.retrieval_sources, source_label]))
        variant_traces = chunk.candidate_trace.get("query_variants")
        if not isinstance(variant_traces, list):
            variant_traces = []
        if query_trace not in variant_traces:
            variant_traces.append(query_trace)
        chunk.candidate_trace["query_variants"] = variant_traces
        existing_chunk = merged.get(dedupe_key)
        if existing_chunk is None:
            merged[dedupe_key] = chunk
            continue
        incoming_similarity = float(chunk.similarity or 0.0)
        existing_similarity = float(existing_chunk.similarity or 0.0)
        existing_chunk.similarity = max(existing_similarity, incoming_similarity)
        existing_chunk.retrieval_sources = list(dict.fromkeys([*existing_chunk.retrieval_sources, *chunk.retrieval_sources]))
        existing_variants = existing_chunk.candidate_trace.get("query_variants")
        if not isinstance(existing_variants, list):
            existing_variants = []
        for variant in variant_traces:
            if variant not in existing_variants:
                existing_variants.append(variant)
        existing_chunk.candidate_trace["query_variants"] = existing_variants
        if incoming_similarity > existing_similarity:
            existing_chunk.candidate_trace.update(chunk.candidate_trace)
    return list(merged.values())


def _build_query_variants(
    message: str,
    understanding: QueryUnderstandingResult | None,
    *,
    rewrite_enabled: bool,
    decomposition_enabled: bool,
) -> list[tuple[str, str]]:
    variants: list[tuple[str, str]] = [("original", str(message or "").strip())]
    if understanding is None:
        return [(kind, query) for kind, query in variants if query]

    semantic_query = understanding.semantic_query.strip()
    if semantic_query:
        variants.append(("semantic", semantic_query))
    for query in understanding.retrieval_plan.rule_expansions:
        variants.append(("rule", str(query).strip()))
    if rewrite_enabled:
        for query in understanding.retrieval_plan.llm_expansions or understanding.rewritten_queries:
            variants.append(("rewrite", str(query).strip()))
        for query in understanding.retrieval_plan.prf_expansions:
            variants.append(("prf", str(query).strip()))
    if decomposition_enabled:
        for query in understanding.decomposition_subqueries:
            variants.append(("decomposition", str(query).strip()))

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, query in variants:
        normalized = " ".join(query.split()).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((kind, normalized))
    return deduped


def _is_comparison_query(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(marker in lowered for marker in [" compare ", " difference ", " vs ", " versus ", "区别", "对比"])


def _extract_comparison_targets(message: str) -> list[str]:
    methods = _mentioned_method_names(message)
    if methods:
        return methods[:2]
    target_map: list[str] = []
    for token in _extract_query_terms(message, max_terms=6):
        if token not in target_map:
            target_map.append(token)
        if len(target_map) >= 2:
            break
    return target_map


def _classify_agentic_query(
    message: str,
    understanding: QueryUnderstandingResult | None,
) -> str:
    lowered = str(message or "").lower()
    if _is_comparison_query(message):
        return "comparison"
    if understanding is not None:
        doc_subtype = str(understanding.retrieval_plan.hard_filters.get("doc_subtype") or "").strip().lower()
        if doc_subtype == "troubleshooting_case":
            return "troubleshooting_why"
    if any(term in lowered for term in ["why", "root cause", "black screen", "no audio", "jitter", "delay", "failed", "failure", "问题", "故障", "排查"]):
        return "troubleshooting_why"
    if any(term in lowered for term in ["configure", "configuration", "setup", "enable", "disable", "deploy", "parameter", "参数", "配置"]):
        return "configuration"
    if _extract_query_terms(message, max_terms=6) or re.search(r"\b\d{3,5}\b", lowered):
        return "lexical_exact"
    return "configuration"


def _tool_order_for_query_class(query_class: str) -> tuple[list[str], str, str]:
    if query_class == "lexical_exact":
        return (["p_bm25", "p_fts", "p_vec"], "exact_match", "lexical")
    if query_class == "troubleshooting_why":
        return (["p_vec", "s_vec", "p_bm25", "s_bm25", "p_fts", "s_fts"], "causal_grounding", "semantic")
    if query_class == "comparison":
        return (["p_vec", "p_bm25", "s_vec", "p_fts"], "balanced_comparison", "compare")
    return (["p_bm25", "p_vec", "p_fts", "s_bm25", "s_vec"], "configuration_support", "lexical")


def _context_keyword_query(ticket_context: list[dict[str, str]] | None) -> str | None:
    parts: list[str] = []
    for item in list(ticket_context or [])[-6:]:
        if not isinstance(item, dict):
            continue
        content = " ".join(str(item.get("content") or "").split()).strip()
        if not content:
            continue
        for token in re.findall(r"[A-Za-z0-9_.-]{3,}", content):
            lowered = token.lower()
            if lowered in _QUERY_STOPWORDS or lowered in parts:
                continue
            parts.append(lowered)
        if len(parts) >= 6:
            break
    if not parts:
        return None
    return " ".join(parts[:6])


def _invoke_agentic_planner(
    *,
    message: str,
    ticket_context: list[dict[str, str]] | None,
    query_understanding: QueryUnderstandingResult | None,
    top_k: int,
    round_index: int,
    product: str | None,
) -> dict[str, Any] | None:
    profile = resolve_model_profile(RAG_AGENT_PLANNER_SCENARIO)
    if not profile.api_key:
        return None
    summary = {
        "query_profile": query_understanding.query_profile if query_understanding is not None else None,
        "semantic_query": query_understanding.semantic_query if query_understanding is not None else None,
        "hard_filters": dict(query_understanding.retrieval_plan.hard_filters) if query_understanding is not None else {},
        "soft_signals": dict(query_understanding.retrieval_plan.soft_signals) if query_understanding is not None else {},
        "rewritten_queries": list(query_understanding.rewritten_queries) if query_understanding is not None else [],
        "decomposition_subqueries": list(query_understanding.decomposition_subqueries) if query_understanding is not None else [],
    }
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=build_rag_agent_planner_system_prompt(
                product_role=build_support_product_rag_role(product),
                product_scope=build_support_product_prompt_scope(product),
            ),
            user_prompt=build_rag_agent_planner_user_prompt(
                message=message,
                ticket_context=ticket_context,
                query_understanding_summary=summary,
                top_k=top_k,
                round_index=round_index,
            ),
        )
    except LlmInvocationError:
        return None
    try:
        payload = json.loads(response.text or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _build_agentic_retrieval_plan(
    *,
    message: str,
    top_k: int,
    query_understanding: QueryUnderstandingResult | None,
    ticket_context: list[dict[str, str]] | None,
    product: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> AgenticRetrievalPlan:
    query_class = _classify_agentic_query(message, query_understanding)
    normalized_message = " ".join(str(message or "").split()).strip()
    if query_class == "lexical_exact" and _is_simple_lexical_query(message):
        return AgenticRetrievalPlan(
            query_class=query_class,
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", normalized_message)],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=bool(ticket_context),
            exact_terms=_extract_query_terms(message, max_terms=6),
            light_path=True,
        )

    _raise_if_cancelled(
        "planner",
        should_cancel=should_cancel,
        record_stage=record_cancel_stage,
    )
    planner_payload = _invoke_agentic_planner(
        message=message,
        ticket_context=ticket_context,
        query_understanding=query_understanding,
        top_k=top_k,
        round_index=1,
        product=product,
    )
    if isinstance(planner_payload, dict):
        query_class = str(planner_payload.get("query_class") or "").strip().lower()
        if query_class in {"lexical_exact", "configuration", "troubleshooting_why", "comparison"}:
            first_pass_tools = [
                str(item).strip()
                for item in planner_payload.get("first_pass_tools") or []
                if str(item).strip()
            ]
            query_variants = [
                (str(item[0]).strip(), str(item[1]).strip())
                for item in planner_payload.get("query_variants") or []
                if isinstance(item, (list, tuple)) and len(item) >= 2 and str(item[1]).strip()
            ]
            if query_variants:
                return AgenticRetrievalPlan(
                    query_class=query_class,
                    first_pass_tools=first_pass_tools or list(_tool_order_for_query_class(query_class)[0]),
                    query_variants=query_variants,
                    decomposition_targets=[
                        str(item).strip()
                        for item in planner_payload.get("decomposition_targets") or []
                        if str(item).strip()
                    ],
                    evidence_goal=str(planner_payload.get("evidence_goal") or _tool_order_for_query_class(query_class)[1]).strip(),
                    recovery_bias=str(planner_payload.get("recovery_bias") or _tool_order_for_query_class(query_class)[2]).strip(),
                    ticket_context_used=bool(ticket_context),
                    exact_terms=_extract_query_terms(message, max_terms=6),
                    light_path=False,
                )

    tool_order, evidence_goal, recovery_bias = _tool_order_for_query_class(query_class)
    query_variants = [("original", normalized_message)]
    simple_lexical_query = query_class == "lexical_exact" and _is_simple_lexical_query(message)
    if query_understanding is not None:
        semantic_query = " ".join(str(query_understanding.semantic_query or "").split()).strip()
        if (
            not simple_lexical_query
            and semantic_query
            and semantic_query.lower() != query_variants[0][1].lower()
        ):
            query_variants.append(("semantic", semantic_query))
        if not simple_lexical_query:
            for rewritten in list(query_understanding.rewritten_queries)[:1]:
                normalized = " ".join(str(rewritten or "").split()).strip()
                if normalized and normalized.lower() not in {item[1].lower() for item in query_variants}:
                    query_variants.append(("rewrite", normalized))
        if query_class == "comparison":
            for subquery in list(query_understanding.decomposition_subqueries)[:2]:
                normalized = " ".join(str(subquery or "").split()).strip()
                if normalized and normalized.lower() not in {item[1].lower() for item in query_variants}:
                    query_variants.append(("decomposition", normalized))
    decomposition_targets = _extract_comparison_targets(message) if query_class == "comparison" else []
    return AgenticRetrievalPlan(
        query_class=query_class,
        first_pass_tools=list(tool_order),
        query_variants=[item for item in query_variants if item[1]],
        decomposition_targets=decomposition_targets,
        evidence_goal=evidence_goal,
        recovery_bias=recovery_bias,
        ticket_context_used=bool(ticket_context),
        exact_terms=_extract_query_terms(message, max_terms=6),
        light_path=False,
    )


def _tool_shadow_limit(limit: int, shadow_ratio_cap: float) -> int:
    if shadow_ratio_cap <= 0:
        return 0
    return max(1, int(max(1, limit) * float(shadow_ratio_cap)))


def _merge_agentic_tool_results(
    *,
    tool_results: dict[str, list[RetrievedChunk]],
    tool_weights: dict[str, float],
    limit: int,
    shadow_ratio_cap: float,
) -> list[RetrievedChunk]:
    safe_limit = max(1, int(limit or 1))
    shadow_limit = _tool_shadow_limit(safe_limit, shadow_ratio_cap)
    merged_scores: dict[str, float] = {}
    merged_chunks: dict[str, RetrievedChunk] = {}
    rrf_k = 60.0

    for tool_name, chunks in tool_results.items():
        weight = max(0.0, float(tool_weights.get(tool_name) or 0.0))
        if weight <= 0:
            continue
        for rank, raw_chunk in enumerate(chunks, start=1):
            chunk = _copy_chunk(raw_chunk)
            if not chunk.index_role:
                chunk.index_role = str(chunk.candidate_trace.get("index_role") or "primary").strip() or "primary"
            chunk.candidate_trace.setdefault("tool_name", tool_name)
            chunk.candidate_trace.setdefault("index_role", chunk.index_role)
            dedupe_key = _chunk_dedupe_key(chunk)
            merged_scores[dedupe_key] = merged_scores.get(dedupe_key, 0.0) + (weight / (rrf_k + rank))
            existing = merged_chunks.get(dedupe_key)
            if existing is None or float(chunk.similarity or 0.0) > float(existing.similarity or 0.0):
                merged_chunks[dedupe_key] = chunk

    ordered_keys = sorted(
        merged_scores.keys(),
        key=lambda key: (
            merged_scores[key],
            float(merged_chunks[key].similarity or 0.0),
        ),
        reverse=True,
    )
    selected: list[RetrievedChunk] = []
    shadow_count = 0
    for key in ordered_keys:
        chunk = merged_chunks[key]
        if str(chunk.index_role or "").strip().lower() == "shadow":
            if shadow_count >= shadow_limit:
                continue
            shadow_count += 1
        chunk.candidate_trace["fusion_score"] = round(float(merged_scores[key]), 6)
        chunk.candidate_trace["index_role"] = chunk.index_role
        selected.append(chunk)
        if len(selected) >= safe_limit:
            break
    return selected


def _exact_terms_in_chunks(exact_terms: list[str], chunks: list[RetrievedChunk]) -> bool:
    if not exact_terms:
        return True
    haystack = " ".join(_chunk_search_text(chunk) for chunk in chunks[:3])
    return all(term.lower() in haystack for term in exact_terms if term)


def _comparison_targets_covered(targets: list[str], chunks: list[RetrievedChunk]) -> bool:
    if not targets:
        return True
    covered: set[str] = set()
    for chunk in chunks:
        method_name = _chunk_method_name(chunk).lower()
        search_text = _chunk_search_text(chunk)
        for target in targets:
            lowered = str(target or "").strip().lower()
            if not lowered:
                continue
            if lowered == method_name.lower() or lowered in search_text:
                covered.add(lowered)
    expected = {str(target or "").strip().lower() for target in targets if str(target or "").strip()}
    return expected.issubset(covered)


def _same_family_only(chunks: list[RetrievedChunk]) -> bool:
    families = {family for family in (_chunk_family_key(chunk) for chunk in chunks) if family}
    return len(families) <= 1


def _judge_agentic_round(
    *,
    message: str,
    query_class: str,
    round_index: int,
    reranked_chunks: list[RetrievedChunk],
    final_chunks: list[RetrievedChunk],
    decomposition_targets: list[str],
    exact_terms: list[str],
    grounded_overlap: bool,
) -> AgenticJudgeDecision:
    top_chunk = reranked_chunks[0] if reranked_chunks else None
    top_score = float(
        (top_chunk.rerank_score if top_chunk and top_chunk.rerank_score is not None else top_chunk.similarity if top_chunk else 0.0)
        or 0.0
    )
    primary_count = sum(1 for chunk in final_chunks if str(chunk.index_role or "").strip().lower() == "primary")
    comparison_covered = _comparison_targets_covered(decomposition_targets, final_chunks)
    exact_match_supported = _exact_terms_in_chunks(exact_terms, final_chunks)
    all_shadow = bool(final_chunks) and all(str(chunk.index_role or "").strip().lower() == "shadow" for chunk in final_chunks)

    if round_index <= 1:
        if query_class == "comparison" and not comparison_covered:
            return AgenticJudgeDecision("recover_once", "comparison_targets_missing", 0.74, "compare_recovery")
        if primary_count == 0:
            recovery = "lexical_recovery" if query_class in {"lexical_exact", "configuration"} else "semantic_recovery"
            return AgenticJudgeDecision("recover_once", "missing_primary_support", 0.72, recovery)
        if query_class == "lexical_exact" and (top_score < 0.32 or not exact_match_supported):
            return AgenticJudgeDecision("recover_once", "low_top1_rerank_score", 0.74, "lexical_recovery")
        if top_score < 0.32:
            recovery = "compare_recovery" if query_class == "comparison" else "semantic_recovery"
            return AgenticJudgeDecision("recover_once", "weak_first_pass_support", 0.7, recovery)
        return AgenticJudgeDecision("answer_now", "sufficient_first_pass_support", 0.9, None)

    if primary_count == 0:
        reason = "weak_shadow_only_support" if all_shadow else "missing_primary_support"
        return AgenticJudgeDecision("escalate", reason, 0.84, None)
    if top_score < 0.25:
        return AgenticJudgeDecision("escalate", "weak_top1_support", 0.82, None)
    if query_class == "comparison" and not comparison_covered:
        return AgenticJudgeDecision("escalate", "comparison_targets_missing", 0.84, None)
    if _same_family_only(final_chunks) and not grounded_overlap:
        return AgenticJudgeDecision("escalate", "single_family_ungrounded", 0.78, None)
    return AgenticJudgeDecision("answer_now", "sufficient_second_pass_support", 0.92, None)


def _tool_weights_for_query_class(query_class: str) -> dict[str, float]:
    if query_class == "lexical_exact":
        return {
            "p_bm25": 1.00,
            "p_fts": 0.90,
            "p_vec": 0.55,
            "s_bm25": 0.45,
            "s_fts": 0.40,
            "s_vec": 0.35,
            "p_keyword": 0.35,
            "s_keyword": 0.20,
        }
    if query_class == "configuration":
        return {
            "p_bm25": 1.00,
            "p_vec": 0.75,
            "p_fts": 0.55,
            "s_bm25": 0.40,
            "s_vec": 0.35,
            "p_keyword": 0.25,
            "s_keyword": 0.15,
        }
    if query_class == "troubleshooting_why":
        return {
            "p_vec": 1.00,
            "s_vec": 0.80,
            "p_bm25": 0.50,
            "s_bm25": 0.30,
            "p_fts": 0.25,
            "s_fts": 0.15,
            "p_keyword": 0.15,
            "s_keyword": 0.10,
        }
    return {
        "p_vec": 0.90,
        "p_bm25": 0.80,
        "s_vec": 0.60,
        "p_fts": 0.30,
        "p_keyword": 0.20,
    }


def _tool_index_role(tool_name: str) -> str:
    return "shadow" if str(tool_name or "").strip().lower().startswith("s_") else "primary"


def _tool_family(tool_name: str) -> str:
    name = str(tool_name or "").strip().lower()
    if name.endswith("_vec"):
        return "vector"
    if name.endswith("_bm25"):
        return "bm25"
    if name.endswith("_fts"):
        return "fts"
    return "keyword"


def _dedupe_agentic_variants(variants: list[tuple[str, str]]) -> list[tuple[str, str]]:
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, query in variants:
        normalized = " ".join(str(query or "").split()).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((str(kind or "").strip() or "original", normalized))
    return deduped


def _agentic_round_tools(
    plan: AgenticRetrievalPlan,
    *,
    round_index: int,
    recovery_action: str | None,
) -> list[str]:
    if plan.light_path:
        if round_index <= 1:
            return ["p_bm25", "p_fts"]
        if recovery_action == "lexical_recovery":
            return ["p_bm25", "p_fts", "p_keyword"]
        return ["p_bm25", "p_fts"]
    if round_index <= 1:
        return list(dict.fromkeys(plan.first_pass_tools))
    if recovery_action == "lexical_recovery":
        return ["p_bm25", "p_fts", "p_vec", "s_bm25"]
    if recovery_action == "semantic_recovery":
        return ["p_vec", "s_vec", "p_bm25", "s_bm25", "p_fts", "s_fts"]
    if recovery_action == "compare_recovery":
        return ["p_vec", "p_bm25", "s_vec", "p_fts"]
    return list(dict.fromkeys(plan.first_pass_tools))


def _agentic_round_variants(
    *,
    message: str,
    plan: AgenticRetrievalPlan,
    round_index: int,
    recovery_action: str | None,
    ticket_context: list[dict[str, str]] | None,
) -> list[tuple[str, str]]:
    variants = list(plan.query_variants)
    if not variants:
        variants = [("original", " ".join(str(message or "").split()).strip())]
    if plan.light_path:
        return _dedupe_agentic_variants(variants)
    existing = {query.lower() for _, query in variants}

    if plan.query_class == "comparison":
        for target in plan.decomposition_targets[:2]:
            normalized = " ".join(str(target or "").split()).strip()
            if normalized and normalized.lower() not in existing:
                variants.append(("decomposition", normalized))
                existing.add(normalized.lower())

    if round_index > 1:
        if recovery_action == "lexical_recovery":
            exact_query = " ".join(plan.exact_terms).strip()
            if exact_query and exact_query.lower() not in existing:
                variants.append(("exact_token", exact_query))
        elif recovery_action == "semantic_recovery":
            context_query = _context_keyword_query(ticket_context)
            if context_query and context_query.lower() not in existing:
                variants.append(("context", context_query))
        elif recovery_action == "compare_recovery":
            context_query = _context_keyword_query(ticket_context)
            if context_query and context_query.lower() not in existing:
                variants.append(("context", context_query))
    return _dedupe_agentic_variants(variants)


def _score_from_candidate_trace(chunk: RetrievedChunk) -> float:
    trace = chunk.candidate_trace if isinstance(chunk.candidate_trace, dict) else {}
    for key in ["raw_score", "bm25_score", "fts_rank", "vector_similarity", "keyword_fallback_hits"]:
        value = trace.get(key)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return float(chunk.similarity or 0.0)


def _select_agentic_final_chunks(
    chunks: list[RetrievedChunk],
    *,
    limit: int,
    query: str,
    shadow_cap: int,
) -> list[RetrievedChunk]:
    if not chunks:
        return []
    ordered = _reorder_chunks_for_rerank(chunks, limit=len(chunks), query=query) or list(chunks)
    results: list[RetrievedChunk] = []
    shadow_count = 0
    for chunk in ordered:
        if str(chunk.index_role or "").strip().lower() == "shadow":
            if shadow_count >= max(0, int(shadow_cap)):
                continue
            shadow_count += 1
        results.append(chunk)
        if len(results) >= max(1, int(limit or 1)):
            break
    return results


def _retrieve_agentic_tool_variant(
    *,
    tool_name: str,
    query_kind: str,
    query_text: str,
    config: dict[str, Any],
    index_role: str,
    round_index: int,
    seeded_chunks: list[RetrievedChunk] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> tuple[str, list[RetrievedChunk], float, float, float, bool]:
    family = _tool_family(tool_name)
    current_tool_label = tool_name
    vector_latency_ms = 0.0
    bm25_latency_ms = 0.0
    keyword_latency_ms = 0.0
    used_seed_tool = False

    stage_name = "vector_embedding" if family == "vector" else f"round_{round_index}_retrieval"
    _raise_if_cancelled(
        stage_name,
        should_cancel=should_cancel,
        record_stage=record_cancel_stage,
    )
    if family == "vector" and not bool(
        config.get("_vector_runtime_available", config.get("vector_enabled", True))
    ):
        return current_tool_label, [], 0.0, 0.0, 0.0, False

    if seeded_chunks:
        raw_chunks = [_copy_chunk(chunk) for chunk in seeded_chunks]
        used_seed_tool = True
    else:
        started_at = time.perf_counter()
        try:
            if family == "vector":
                raw_chunks = _retrieve_chunks(
                    query_text,
                    config,
                    limit=int(config.get("vector_candidate_k") or config.get("top_k") or 5),
                    index_role=index_role,
                )
                vector_latency_ms += (time.perf_counter() - started_at) * 1000
            elif family == "bm25":
                raw_chunks = _retrieve_bm25_chunks(
                    query_text,
                    config,
                    limit=int(config.get("bm25_candidate_k") or config.get("top_k") or 5),
                    index_role=index_role,
                )
                bm25_latency_ms += (time.perf_counter() - started_at) * 1000
            elif family == "fts":
                raw_chunks = _retrieve_fts_chunks(
                    query_text,
                    config,
                    limit=int(config.get("fts_candidate_k") or config.get("keyword_candidate_k") or config.get("top_k") or 5),
                    index_role=index_role,
                )
                bm25_latency_ms += (time.perf_counter() - started_at) * 1000
            else:
                raw_chunks = _retrieve_keyword_chunks(
                    query_text,
                    config,
                    limit=int(config.get("keyword_candidate_k") or config.get("top_k") or 5),
                    index_role=index_role,
                )
                keyword_latency_ms += (time.perf_counter() - started_at) * 1000
        except Exception as exc:
            if family == "vector":
                config["_vector_runtime_available"] = False
                _record_runtime_capability_failure(
                    "vector",
                    provider=str(config.get("embedding_provider") or ""),
                    error=exc,
                )
                logger.warning("RAG %s retrieval failed for %s query: %s", tool_name, query_kind, exc)
                return current_tool_label, [], vector_latency_ms, bm25_latency_ms, keyword_latency_ms, used_seed_tool
            if family not in {"bm25", "fts"}:
                logger.warning("RAG %s retrieval failed for %s query: %s", tool_name, query_kind, exc)
                return current_tool_label, [], vector_latency_ms, bm25_latency_ms, keyword_latency_ms, used_seed_tool
            logger.warning("RAG %s retrieval failed for %s query, trying keyword fallback: %s", tool_name, query_kind, exc)
            started_at = time.perf_counter()
            try:
                raw_chunks = _retrieve_keyword_chunks(
                    query_text,
                    config,
                    limit=int(config.get("keyword_candidate_k") or config.get("top_k") or 5),
                    index_role=index_role,
                )
                keyword_latency_ms += (time.perf_counter() - started_at) * 1000
                current_tool_label = f"{'s' if index_role == 'shadow' else 'p'}_keyword"
            except Exception as keyword_exc:
                logger.warning("RAG keyword retrieval failed for %s query: %s", query_kind, keyword_exc)
                return current_tool_label, [], vector_latency_ms, bm25_latency_ms, keyword_latency_ms, used_seed_tool

    for chunk in raw_chunks:
        chunk.index_role = index_role
        chunk.retrieval_sources = list(dict.fromkeys([*chunk.retrieval_sources, current_tool_label]))
        chunk.candidate_trace["tool_name"] = current_tool_label
        chunk.candidate_trace["query_kind"] = query_kind
        chunk.candidate_trace["query_round"] = round_index
        chunk.candidate_trace["raw_score"] = _score_from_candidate_trace(chunk)
        chunk.candidate_trace["index_role"] = index_role

    return current_tool_label, raw_chunks, vector_latency_ms, bm25_latency_ms, keyword_latency_ms, used_seed_tool


def _execute_agentic_round(
    *,
    message: str,
    config: dict[str, Any],
    plan: AgenticRetrievalPlan,
    round_index: int,
    retrieval_plan: RetrievalPlan,
    query_understanding: QueryUnderstandingResult | None,
    ticket_context: list[dict[str, str]] | None,
    recovery_action: str | None = None,
    seed_tool_results: dict[str, list[RetrievedChunk]] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> AgenticRoundResult:
    _raise_if_cancelled(
        f"round_{round_index}_retrieval",
        should_cancel=should_cancel,
        record_stage=record_cancel_stage,
    )
    if not _runtime_capability_available("vector", provider=str(config.get("embedding_provider") or "")):
        config["_vector_runtime_available"] = False
    if not _runtime_capability_available("rerank", provider=str(config.get("rerank_provider") or "")):
        config["_rerank_runtime_available"] = False

    tool_names = _agentic_round_tools(plan, round_index=round_index, recovery_action=recovery_action)
    query_variants = _agentic_round_variants(
        message=message,
        plan=plan,
        round_index=round_index,
        recovery_action=recovery_action,
        ticket_context=ticket_context,
    )
    tool_weights = _tool_weights_for_query_class(plan.query_class)
    tool_results: dict[str, list[RetrievedChunk]] = {}
    vector_candidate_ids: set[str] = set()
    bm25_candidate_ids: set[str] = set()
    vector_latency_ms = 0.0
    bm25_latency_ms = 0.0
    keyword_latency_ms = 0.0
    variant_config = dict(config)
    variant_config["_retrieval_plan"] = retrieval_plan
    used_seed_tools: list[str] = []

    def _consume_tool_result(
        *,
        family: str,
        tool_name: str,
        query_kind: str,
        query_text: str,
        result: tuple[str, list[RetrievedChunk], float, float, float, bool],
    ) -> None:
        nonlocal vector_latency_ms, bm25_latency_ms, keyword_latency_ms
        current_tool_label, raw_chunks, vector_ms, bm25_ms, keyword_ms, used_seed_tool = result
        vector_latency_ms += vector_ms
        bm25_latency_ms += bm25_ms
        keyword_latency_ms += keyword_ms
        if used_seed_tool:
            used_seed_tools.append(tool_name)
        if not raw_chunks:
            return
        merged_tool_chunks = _merge_variant_chunks(
            tool_results.get(current_tool_label, []),
            raw_chunks,
            source_label=current_tool_label,
            query_variant=query_text,
            query_kind=query_kind,
        )
        tool_results[current_tool_label] = merged_tool_chunks
        target_set = vector_candidate_ids if family == "vector" else bm25_candidate_ids
        for chunk in merged_tool_chunks:
            dedupe_key = _chunk_dedupe_key(chunk)
            if dedupe_key:
                target_set.add(dedupe_key)

    if plan.light_path and round_index == 1 and len(query_variants) == 1 and set(tool_names) == {"p_bm25", "p_fts"}:
        query_kind, query_text = query_variants[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_map: dict[Future[tuple[str, list[RetrievedChunk], float, float, float, bool]], tuple[str, str]] = {}
            for tool_name in tool_names:
                family = _tool_family(tool_name)
                future_map[
                    executor.submit(
                        _retrieve_agentic_tool_variant,
                        tool_name=tool_name,
                        query_kind=query_kind,
                        query_text=query_text,
                        config=variant_config,
                        index_role=_tool_index_role(tool_name),
                        round_index=round_index,
                        seeded_chunks=None,
                        should_cancel=should_cancel,
                        record_cancel_stage=record_cancel_stage,
                    )
                ] = (tool_name, family)
            for future, (tool_name, family) in future_map.items():
                _consume_tool_result(
                    family=family,
                    tool_name=tool_name,
                    query_kind=query_kind,
                    query_text=query_text,
                    result=future.result(),
                )
    else:
        for tool_name in tool_names:
            family = _tool_family(tool_name)
            if family == "vector" and not bool(
                variant_config.get("_vector_runtime_available", variant_config.get("vector_enabled", True))
            ):
                continue
            index_role = _tool_index_role(tool_name)
            for query_kind, query_text in query_variants:
                if family == "vector" and not bool(
                    variant_config.get("_vector_runtime_available", variant_config.get("vector_enabled", True))
                ):
                    break
                seeded_chunks = None
                if round_index == 1 and query_kind == "original":
                    seeded_chunks = list((seed_tool_results or {}).get(tool_name) or [])
                result = _retrieve_agentic_tool_variant(
                    tool_name=tool_name,
                    query_kind=query_kind,
                    query_text=query_text,
                    config=variant_config,
                    index_role=index_role,
                    round_index=round_index,
                    seeded_chunks=seeded_chunks,
                    should_cancel=should_cancel,
                    record_cancel_stage=record_cancel_stage,
                )
                _consume_tool_result(
                    family=family,
                    tool_name=tool_name,
                    query_kind=query_kind,
                    query_text=query_text,
                    result=result,
                )

    retrieved_chunks = _merge_agentic_tool_results(
        tool_results=tool_results,
        tool_weights=tool_weights,
        limit=int(config.get("fusion_candidate_k") or config.get("top_k") or 5),
        shadow_ratio_cap=float(config.get("agent_shadow_ratio_cap") or 0.4),
    )
    reranked_chunks = list(retrieved_chunks)
    rerank_info: dict[str, Any] = {
        "hints": {"language": None, "method_name": None, "intent_terms": []},
        "applied_filter": False,
        "filter_type": None,
        "filtered_candidate_count": 0,
        "post_rerank_count": 0,
        "candidate_reasons": {},
    }
    rerank_latency_ms = 0.0
    if reranked_chunks:
        _raise_if_cancelled(
            "rerank",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        rerank_started_at = time.perf_counter()
        reranked_chunks, rerank_info = _metadata_rerank(
            query=message,
            chunks=reranked_chunks,
            top_k=int(config.get("fusion_candidate_k") or config.get("top_k") or 5),
            retrieval_plan=retrieval_plan,
            query_understanding=query_understanding,
        )
        rerank_latency_ms += (time.perf_counter() - rerank_started_at) * 1000
        reranked_chunks = _reorder_chunks_for_rerank(
            reranked_chunks or retrieved_chunks,
            limit=int(config.get("rerank_top_n") or len(retrieved_chunks) or 1),
            query=message,
        ) or reranked_chunks or retrieved_chunks
        if (
            not plan.light_path
            and bool(config.get("_rerank_runtime_available", config.get("rerank_enabled", True)))
        ):
            _raise_if_cancelled(
                "rerank",
                should_cancel=should_cancel,
                record_stage=record_cancel_stage,
            )
            rerank_started_at = time.perf_counter()
            reranked_chunks = _rerank_chunks(
                message,
                reranked_chunks,
                config,
                limit=int(config.get("rerank_top_n") or len(reranked_chunks) or 1),
            ) or reranked_chunks
            rerank_latency_ms += (time.perf_counter() - rerank_started_at) * 1000

    final_shadow_cap = int(config.get("agent_final_shadow_cap") or 1)
    recovery_shadow_cap = int(config.get("agent_recovery_shadow_cap") or 2)
    final_chunks = _select_agentic_final_chunks(
        reranked_chunks,
        limit=int(config.get("top_k") or 5),
        query=message,
        shadow_cap=final_shadow_cap,
    )
    if sum(1 for chunk in final_chunks if str(chunk.index_role or "").strip().lower() == "primary") == 0:
        final_chunks = _select_agentic_final_chunks(
            reranked_chunks,
            limit=int(config.get("top_k") or 5),
            query=message,
            shadow_cap=recovery_shadow_cap,
        )

    grounded_overlap = _has_grounded_keyword_overlap(message, final_chunks)
    judge = _judge_agentic_round(
        message=message,
        query_class=plan.query_class,
        round_index=round_index,
        reranked_chunks=reranked_chunks,
        final_chunks=final_chunks,
        decomposition_targets=plan.decomposition_targets,
        exact_terms=plan.exact_terms,
        grounded_overlap=grounded_overlap,
    )
    return AgenticRoundResult(
        retrieved_chunks=retrieved_chunks,
        reranked_chunks=reranked_chunks,
        final_chunks=final_chunks,
        rerank_info=rerank_info,
        judge=judge,
        iteration_trace=AgenticIterationTrace(
            round_index=round_index,
            tool_names=list(dict.fromkeys(tool_names)),
            query_variants=[kind for kind, _ in query_variants],
            selected_chunk_ids=[chunk.chunk_id for chunk in final_chunks if chunk.chunk_id],
            decision=judge.decision,
            recovery_action=judge.recovery_action,
        ),
        vector_candidate_count=len(vector_candidate_ids),
        bm25_candidate_count=len(bm25_candidate_ids),
        vector_latency_ms=round(vector_latency_ms, 2),
        bm25_latency_ms=round(bm25_latency_ms, 2),
        keyword_latency_ms=round(keyword_latency_ms, 2),
        rerank_latency_ms=round(rerank_latency_ms, 2),
        used_seed_tools=list(dict.fromkeys(used_seed_tools)),
    )


def _normalize_metadata_filter_value(key: str, value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if key == "language":
        return _normalize_language_hint(raw)
    if key == "protocol":
        return lowered.replace("_", "-")
    if key == "doc_subtype":
        return lowered.replace("-", "_")
    if key in {"product", "source_family"}:
        return lowered.replace(" ", "-")
    return raw


def _query_understanding_meta(
    understanding: QueryUnderstandingResult | None,
    rerank_info: dict[str, Any],
) -> dict[str, Any]:
    payload = rerank_info.get("query_understanding") if isinstance(rerank_info.get("query_understanding"), dict) else {}
    if payload:
        fallback_profile = understanding.query_profile if understanding is not None else ""
        fallback_mode = understanding.fallback_mode if understanding is not None else ""
        return {
            "query_profile": str(payload.get("query_profile") or fallback_profile),
            "glossary_hit_terms": list(payload.get("glossary_hit_terms") or []),
            "applied_hard_filters": dict(payload.get("applied_hard_filters") or {}),
            "applied_soft_signals": dict(payload.get("applied_soft_signals") or {}),
            "fallback_mode": str(payload.get("fallback_mode") or fallback_mode),
            "dictionary_hits": list(payload.get("dictionary_hits") or []),
            "rule_expansions": list(payload.get("rule_expansions") or []),
            "llm_expansions": list(payload.get("llm_expansions") or []),
            "prf_expansions": list(payload.get("prf_expansions") or []),
            "hard_filter_sources": dict(payload.get("hard_filter_sources") or {}),
            "cache_hit": bool(payload.get("cache_hit")),
            "prf_used": bool(payload.get("prf_used")),
        }
    if understanding is None:
        return {
            "query_profile": "",
            "glossary_hit_terms": [],
            "applied_hard_filters": {},
            "applied_soft_signals": {},
            "fallback_mode": "disabled",
            "dictionary_hits": [],
            "rule_expansions": [],
            "llm_expansions": [],
            "prf_expansions": [],
            "hard_filter_sources": {},
            "cache_hit": False,
            "prf_used": False,
        }
    return {
        "query_profile": understanding.query_profile,
        "glossary_hit_terms": list(understanding.canonical_terms),
        "applied_hard_filters": dict(understanding.retrieval_plan.hard_filters),
        "applied_soft_signals": dict(understanding.retrieval_plan.soft_signals),
        "fallback_mode": understanding.fallback_mode,
        "dictionary_hits": list(understanding.dictionary_hits),
        "rule_expansions": list(understanding.retrieval_plan.rule_expansions),
        "llm_expansions": list(understanding.retrieval_plan.llm_expansions),
        "prf_expansions": list(understanding.retrieval_plan.prf_expansions),
        "hard_filter_sources": dict(understanding.retrieval_plan.hard_filter_sources),
        "cache_hit": bool(understanding.cache_hit),
        "prf_used": bool(understanding.retrieval_plan.prf_used),
    }


def _drain_embedding_request_meta(provider: Any) -> list[dict[str, Any]]:
    try:
        raw_items = provider.drain_request_log()
    except Exception:
        return []
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _import_psycopg() -> Any:
    import psycopg

    return psycopg


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.10f}" for v in values) + "]"


def _config_retrieval_plan(config: dict[str, Any]) -> RetrievalPlan | None:
    plan = config.get("_retrieval_plan")
    return plan if isinstance(plan, RetrievalPlan) else None


def _metadata_filter_clauses(psycopg_sql: Any, filters: dict[str, str], *, metadata_ref: str) -> tuple[Any, list[Any]]:
    sql = psycopg_sql
    clauses: list[Any] = []
    params: list[Any] = []
    for key, value in filters.items():
        clauses.append(
            sql.SQL("LOWER(COALESCE({} ->> {}, '')) = %s").format(
                sql.SQL(metadata_ref),
                sql.Literal(key),
            )
        )
        params.append(str(value or "").strip().lower())
    if not clauses:
        return sql.SQL(""), []
    return sql.SQL(" AND ") + sql.SQL(" AND ").join(clauses), params


def _split_table_name(raw_value: str, default_schema: str = "supportportal") -> tuple[str, str]:
    value = (raw_value or "").strip()
    if not value:
        return default_schema, DEFAULT_PGVECTOR_TABLE
    if "." not in value:
        return default_schema, value
    schema, table_name = value.split(".", 1)
    schema = schema.strip() or default_schema
    table_name = table_name.strip() or DEFAULT_PGVECTOR_TABLE
    return schema, table_name


def _safe_int_env(key: str, default_value: int) -> int:
    raw = (os.getenv(key, "") or "").strip()
    if not raw:
        return default_value
    try:
        parsed = int(raw)
    except ValueError:
        return default_value
    return parsed if parsed > 0 else default_value


def _safe_float_env(key: str, default_value: float) -> float:
    raw = (os.getenv(key, "") or "").strip()
    if not raw:
        return default_value
    try:
        parsed = float(raw)
    except ValueError:
        return default_value
    return parsed if parsed > 0 else default_value


def _build_heading(chunk: RetrievedChunk) -> str:
    heading_items = [item for item in [chunk.h1, chunk.h2, chunk.h3] if item]
    return " > ".join(heading_items) if heading_items else "Unknown heading"


def _normalize_language_hint(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    mapping = {
        "go": "go",
        "golang": "go",
        "node.js": "nodejs",
        "nodejs": "nodejs",
        "javascript": "nodejs",
        "php": "php",
        "python": "python",
        "python3": "python",
        "java": "java",
        "c++": "cpp",
        "cpp": "cpp",
    }
    return mapping.get(raw, raw or None)


def _chunk_metadata_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    return []


def _extract_technical_query_terms(query: str) -> tuple[str, ...]:
    lower = str(query or "").lower()
    terms: list[str] = []
    for label, patterns in [
        ("startup_delay", [r"延迟", r"late", r"delay", r"latency", r"startup"]),
        ("missing initial content", [r"丢", r"缺失", r"missing", r"truncated", r"开头"]),
        ("first frame delayed", [r"第一帧", r"first frame"]),
        ("cloud transcoder", [r"cloud transcoder"]),
        ("aws ivs", [r"aws ivs", r"\bivs\b"]),
        ("rtmp", [r"\brtmp\b"]),
        ("queue delay", [r"\bqueue\b", r"队列", r"background job", r"worker"]),
        ("create request", [r"create request", r"\bcreate\b"]),
    ]:
        if any(re.search(pattern, lower) for pattern in patterns):
            terms.append(label)
    return tuple(terms)


def _technical_intent_chunk_types(intent_terms: tuple[str, ...]) -> set[str]:
    mapping = {
        "troubleshooting": {"troubleshooting_procedure"},
        "decision_logic": {"decision_logic"},
        "best_practice": {"best_practice"},
        "root_cause": {"root_cause_summary", "issue_summary"},
    }
    chunk_types: set[str] = set()
    for term in intent_terms:
        chunk_types.update(mapping.get(term, set()))
    return chunk_types


def _is_technical_case_chunk(metadata: dict[str, Any]) -> bool:
    if not isinstance(metadata, dict):
        return False
    if str(metadata.get("doc_subtype") or "").strip() == "troubleshooting_case":
        return True
    return str(metadata.get("source_type") or "").strip() == "technical_article_api"


def _extract_metadata_hints(query: str) -> MetadataHints:
    text = str(query or "")
    lower = text.lower()
    language: str | None = None
    for pattern, normalized in [
        (r"\bnode\.js\b|\bnodejs\b", "nodejs"),
        (r"\bgolang\b|\bgo\b", "go"),
        (r"\bphp\b", "php"),
        (r"\bpython3\b|\bpython\b", "python"),
        (r"\bjava\b", "java"),
        (r"\bc\+\+\b|\bcpp\b", "cpp"),
    ]:
        if re.search(pattern, lower):
            language = normalized
            break
    method_name: str | None = None
    if re.search(r"\bBuildTokenWithUidAndPrivilege\b", text, flags=re.IGNORECASE):
        method_name = "BuildTokenWithUidAndPrivilege"
    elif re.search(r"\bBuildTokenWithUid\b", text, flags=re.IGNORECASE):
        method_name = "BuildTokenWithUid"
    intents: list[str] = []
    for label, patterns in [
        ("docker", [r"\bdocker\b"]),
        ("npm", [r"\bnpm\b"]),
        ("faq", [r"\bfaq\b", r"frequently asked questions", r"常见问题"]),
        ("compatibility", [r"\bcompatibility\b", r"兼容性"]),
        ("parameter", [r"\bparameter\b", r"\bparameters\b", r"\bparam\b", r"参数"]),
        ("wildcard", [r"\bwildcard\b"]),
        ("uid=0", [r"uid\s*=\s*0"]),
        ("api reference", [r"\bapi reference\b", r"\bapi\b"]),
        ("troubleshooting", [r"怎么排查", r"如何排查", r"排查", r"\btroubleshoot\b", r"\binvestigate\b", r"\bdebug\b"]),
        ("decision_logic", [r"怎么判断", r"如何判断", r"责任边界", r"说明什么", r"\bdetermine\b", r"\binterpret\b"]),
        ("best_practice", [r"怎么避免", r"如何避免", r"预防", r"best practice", r"\bavoid\b"]),
        ("root_cause", [r"为什么", r"原因", r"root cause", r"什么原因"]),
    ]:
        if any(re.search(pattern, lower) for pattern in patterns):
            intents.append(label)
    return MetadataHints(
        language=language,
        method_name=method_name,
        intent_terms=tuple(intents),
        technical_terms=_extract_technical_query_terms(text),
    )


def _mentioned_method_names(query: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    raw_query = str(query or "")
    for method_name in ["BuildTokenWithUidAndPrivilege", "BuildTokenWithUid"]:
        pattern = rf"\b{re.escape(method_name)}\b"
        for match in re.finditer(pattern, raw_query, flags=re.IGNORECASE):
            matches.append((match.start(), method_name))
    methods: list[str] = []
    seen: set[str] = set()
    for _, method_name in sorted(matches, key=lambda item: item[0]):
        if method_name in seen:
            continue
        seen.add(method_name)
        methods.append(method_name)
    return methods


def _is_method_comparison_query(query: str, mentioned_methods: list[str]) -> bool:
    if len(mentioned_methods) < 2:
        return False
    return any(
        marker in str(query or "").lower()
        for marker in ["区别", "difference", "compare", "vs", "versus", " and "]
    )


def _is_generic_token_generation_query(query: str, hints: MetadataHints) -> bool:
    lower = str(query or "").lower()
    if hints.method_name or _mentioned_method_names(query):
        return False
    if any(term in hints.intent_terms for term in ("parameter", "wildcard", "compatibility", "api reference")):
        return False
    if any(
        marker in lower
        for marker in ["privilege", "permission", "advanced", "granular", "fine-grained", "权限", "高级"]
    ):
        return False
    has_token = "token" in lower
    has_generation_intent = any(marker in lower for marker in ["generate", "生成", "create token", "token server"])
    return has_token and has_generation_intent


def _metadata_rerank(
    *,
    query: str,
    chunks: list[RetrievedChunk],
    top_k: int,
    hints: MetadataHints | None = None,
    retrieval_plan: RetrievalPlan | None = None,
    query_understanding: QueryUnderstandingResult | None = None,
) -> tuple[list[RetrievedChunk], dict[str, Any]]:
    resolved_hints = hints or _extract_metadata_hints(query)
    mentioned_methods = _mentioned_method_names(query)
    comparison_mode = _is_method_comparison_query(query, mentioned_methods)
    generic_token_generation_query = _is_generic_token_generation_query(query, resolved_hints)
    plan_hard_filters = dict(retrieval_plan.hard_filters) if retrieval_plan is not None else {}
    plan_soft_signals = dict(retrieval_plan.soft_signals) if retrieval_plan is not None else {}
    filtered_chunks = list(chunks)
    filter_type: str | None = None
    normalized_language = _normalize_language_hint(plan_hard_filters.get("language") or resolved_hints.language)
    method_filter_name = None if comparison_mode else str(
        plan_hard_filters.get("method_name") or resolved_hints.method_name or ""
    ).strip() or None
    technical_intent_chunk_types = _technical_intent_chunk_types(resolved_hints.intent_terms)
    applied_hard_filters: dict[str, str] = {}
    if normalized_language:
        applied_hard_filters["language"] = normalized_language
    if method_filter_name:
        applied_hard_filters["method_name"] = method_filter_name
    for key, value in plan_hard_filters.items():
        if key in {"language", "method_name"}:
            continue
        normalized_value = _normalize_metadata_filter_value(key, value)
        if normalized_value:
            applied_hard_filters[key] = normalized_value

    if applied_hard_filters:
        hard_filtered = []
        for chunk in chunks:
            metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
            matches = True
            for key, expected in applied_hard_filters.items():
                actual = _normalize_metadata_filter_value(key, metadata.get(key))
                if actual != expected:
                    matches = False
                    break
            if matches:
                hard_filtered.append(chunk)
        if len(hard_filtered) >= min(max(1, int(top_k)), 2):
            filtered_chunks = hard_filtered
            filter_labels = ["method" if key == "method_name" else key for key in sorted(applied_hard_filters.keys())]
            filter_type = "+".join(filter_labels)
        else:
            applied_hard_filters = {}
    if filter_type is None and technical_intent_chunk_types:
        technical_filtered = [
            chunk
            for chunk in filtered_chunks
            if _is_technical_case_chunk(chunk.metadata)
            and str(chunk.metadata.get("chunk_type") or "").strip().lower() in technical_intent_chunk_types
        ]
        if technical_filtered:
            filtered_chunks = technical_filtered
            filter_type = "technical_intent"

    candidate_reasons: dict[str, list[str]] = {}
    for chunk in filtered_chunks:
        reasons: list[str] = []
        boost = 0.0
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        chunk_language = _normalize_language_hint(metadata.get("language"))
        chunk_method_name = str(metadata.get("method_name") or "").strip() or None
        chunk_type = str(metadata.get("chunk_type") or "").strip().lower()
        section_path = " > ".join(_chunk_metadata_list(metadata.get("section_path"))).lower()
        topics = [item.lower() for item in _chunk_metadata_list(metadata.get("topic"))]
        use_case = str(metadata.get("use_case") or "").strip().lower()
        issue_category = str(metadata.get("issue_category") or "").strip().lower()
        symptoms = [item.lower() for item in _chunk_metadata_list(metadata.get("symptoms"))]
        keywords = [item.lower() for item in _chunk_metadata_list(metadata.get("keywords"))]
        external_service = str(metadata.get("external_service") or "").strip().lower()
        protocol = str(metadata.get("protocol") or "").strip().lower()
        product = _normalize_metadata_filter_value("product", metadata.get("product")) or ""
        technical_terms = {item.lower() for item in resolved_hints.technical_terms}
        text_lower = chunk.text.lower()

        if normalized_language and chunk_language == normalized_language:
            boost += 2.0
            reasons.append(f"language:{normalized_language}")
        if comparison_mode and chunk_method_name in mentioned_methods:
            boost += 1.5
            reasons.append(f"method_compare:{chunk_method_name}")
        elif resolved_hints.method_name and chunk_method_name == resolved_hints.method_name:
            boost += 2.5
            reasons.append(f"method_name:{resolved_hints.method_name}")
        if generic_token_generation_query and use_case == "basic_authentication":
            boost += 0.8
            reasons.append("intent:generic_token_basic_auth")
        if generic_token_generation_query and use_case == "advanced_permissions":
            boost -= 0.45
            reasons.append("intent:generic_token_advanced_penalty")
        if "docker" in resolved_hints.intent_terms and (
            use_case == "docker_deployment" or "docker" in topics or "docker" in section_path
        ):
            boost += 1.0
            reasons.append("use_case:docker_deployment" if use_case == "docker_deployment" else "topic:docker")
        if "npm" in resolved_hints.intent_terms and (
            use_case == "npm_deployment" or "npm" in topics or "npm" in section_path
        ):
            boost += 1.0
            reasons.append("use_case:npm_deployment" if use_case == "npm_deployment" else "topic:npm")
        if "parameter" in resolved_hints.intent_terms and chunk_type == "api_params":
            boost += 1.2
            reasons.append("chunk_type:api_params")
        if "wildcard" in resolved_hints.intent_terms and (
            use_case == "wildcard_tokens" or "wildcard" in topics or "wildcard" in section_path
        ):
            boost += 1.0
            reasons.append("use_case:wildcard_tokens")
        if "uid=0" in resolved_hints.intent_terms and "uid=0" in text_lower:
            boost += 1.2
            reasons.append("text:uid=0")
        if "compatibility" in resolved_hints.intent_terms and chunk_type == "compatibility":
            boost += 1.0
            reasons.append("chunk_type:compatibility")
        if "faq" in resolved_hints.intent_terms and chunk_type == "faq_index":
            boost += 1.0
            reasons.append("chunk_type:faq_index")
        if "api reference" in resolved_hints.intent_terms and (
            chunk_type.startswith("api_") or "api reference" in section_path
        ):
            boost += 0.8
            reasons.append(f"chunk_type:{chunk_type}" if chunk_type.startswith("api_") else "section_path:api reference")
        if _is_technical_case_chunk(metadata):
            if "troubleshooting" in resolved_hints.intent_terms and chunk_type == "troubleshooting_procedure":
                boost += 2.0
                reasons.append("intent:troubleshooting")
            if "decision_logic" in resolved_hints.intent_terms and chunk_type == "decision_logic":
                boost += 2.2
                reasons.append("intent:decision_logic")
            if "best_practice" in resolved_hints.intent_terms and chunk_type == "best_practice":
                boost += 2.0
                reasons.append("intent:best_practice")
            if "root_cause" in resolved_hints.intent_terms and chunk_type == "root_cause_summary":
                boost += 2.0
                reasons.append("intent:root_cause")
            if "root_cause" in resolved_hints.intent_terms and chunk_type == "issue_summary":
                boost += 1.0
                reasons.append("intent:root_cause_support")
            if "startup_delay" in technical_terms and issue_category == "startup_delay":
                boost += 1.0
                reasons.append("issue_category:startup_delay")
            for symptom in symptoms:
                if symptom in technical_terms:
                    boost += 1.1
                    reasons.append(f"symptom:{symptom}")
            for keyword in keywords:
                if keyword in technical_terms:
                    boost += 0.8
                    reasons.append(f"keyword:{keyword}")
            if external_service and external_service in technical_terms:
                boost += 0.6
                reasons.append(f"external_service:{metadata.get('external_service')}")
            if protocol and protocol in technical_terms:
                boost += 0.6
                reasons.append(f"protocol:{str(metadata.get('protocol') or '').upper()}")

        for soft_signal in _chunk_metadata_list(plan_soft_signals.get("chunk_type")):
            normalized_signal = soft_signal.strip().lower()
            if normalized_signal and chunk_type == normalized_signal:
                boost += 1.1
                reasons.append(f"plan_chunk_type:{normalized_signal}")
        for soft_signal in _chunk_metadata_list(plan_soft_signals.get("section_path")):
            normalized_signal = soft_signal.strip().lower()
            if normalized_signal and normalized_signal in section_path:
                boost += 0.7
                reasons.append(f"plan_section_path:{normalized_signal}")
        for soft_signal in _chunk_metadata_list(plan_soft_signals.get("topic")):
            normalized_signal = soft_signal.strip().lower()
            if normalized_signal and normalized_signal in topics:
                boost += 0.7
                reasons.append(f"plan_topic:{normalized_signal}")
        for soft_signal in _chunk_metadata_list(plan_soft_signals.get("use_case")):
            normalized_signal = soft_signal.strip().lower()
            if normalized_signal and use_case == normalized_signal:
                boost += 0.8
                reasons.append(f"plan_use_case:{normalized_signal}")
        for soft_signal in _chunk_metadata_list(plan_soft_signals.get("issue_category")):
            normalized_signal = soft_signal.strip().lower()
            if normalized_signal and issue_category == normalized_signal:
                boost += 0.8
                reasons.append(f"plan_issue_category:{normalized_signal}")
        for soft_signal in _chunk_metadata_list(plan_soft_signals.get("symptoms")):
            normalized_signal = soft_signal.strip().lower()
            if normalized_signal and normalized_signal in symptoms:
                boost += 1.0
                reasons.append(f"plan_symptom:{normalized_signal}")
        for soft_signal in _chunk_metadata_list(plan_soft_signals.get("keywords")):
            normalized_signal = soft_signal.strip().lower()
            if normalized_signal and (normalized_signal in keywords or normalized_signal in text_lower):
                boost += 0.6
                reasons.append(f"plan_keyword:{normalized_signal}")
        for soft_signal in _chunk_metadata_list(plan_soft_signals.get("external_service")):
            normalized_signal = soft_signal.strip().lower()
            if normalized_signal and external_service == normalized_signal:
                boost += 0.6
                reasons.append(f"plan_external_service:{normalized_signal}")
        if applied_hard_filters.get("product") and product == applied_hard_filters["product"]:
            boost += 0.5
            reasons.append(f"plan_product:{applied_hard_filters['product']}")

        chunk.rerank_score = round(float(chunk.similarity) + boost, 4)
        chunk.rerank_reasons = reasons
        candidate_reasons[chunk.chunk_id] = list(reasons)

    ordered = sorted(
        filtered_chunks,
        key=lambda chunk: (
            float(chunk.rerank_score or chunk.similarity or 0.0),
            float(chunk.similarity or 0.0),
        ),
        reverse=True,
    )
    ordered_chunk_ids = {chunk.chunk_id for chunk in ordered if chunk.chunk_id}
    for rank, chunk in enumerate(ordered, start=1):
        chunk.candidate_trace["metadata_rank"] = rank
        chunk.candidate_trace["metadata_score"] = chunk.rerank_score
    if filter_type is not None:
        for chunk in chunks:
            if chunk.chunk_id and chunk.chunk_id not in ordered_chunk_ids:
                chunk.candidate_trace["metadata_filtered_out"] = True
    return ordered, {
        "hints": {
            "language": normalized_language,
            "method_name": resolved_hints.method_name,
            "intent_terms": list(resolved_hints.intent_terms),
            "technical_terms": list(resolved_hints.technical_terms),
        },
        "applied_filter": filter_type is not None,
        "filter_type": filter_type,
        "filtered_candidate_count": len(filtered_chunks),
        "post_rerank_count": len(ordered),
        "candidate_reasons": candidate_reasons,
        "query_understanding": {
            "query_profile": query_understanding.query_profile if query_understanding is not None else "",
            "glossary_hit_terms": list(query_understanding.canonical_terms) if query_understanding is not None else [],
            "applied_hard_filters": applied_hard_filters,
            "applied_soft_signals": plan_soft_signals,
            "fallback_mode": query_understanding.fallback_mode if query_understanding is not None else "disabled",
            "dictionary_hits": list(query_understanding.dictionary_hits) if query_understanding is not None else [],
            "rule_expansions": list(retrieval_plan.rule_expansions) if retrieval_plan is not None else [],
            "llm_expansions": list(retrieval_plan.llm_expansions) if retrieval_plan is not None else [],
            "prf_expansions": list(retrieval_plan.prf_expansions) if retrieval_plan is not None else [],
            "hard_filter_sources": dict(retrieval_plan.hard_filter_sources) if retrieval_plan is not None else {},
            "cache_hit": bool(retrieval_plan.cache_hit) if retrieval_plan is not None else False,
            "prf_used": bool(retrieval_plan.prf_used) if retrieval_plan is not None else False,
        },
    }


def _get_rag_config(top_k: int | None = None) -> dict[str, Any]:
    dsn = (os.getenv("PGVECTOR_DSN") or "").strip()
    answer_profile = resolve_model_profile(RAG_ANSWER_SCENARIO)
    compression_profile = resolve_model_profile(RAG_CONTEXT_COMPRESSION_SCENARIO)
    final_top_k = max(1, int(top_k)) if top_k is not None else _safe_int_env("RAG_TOP_K", 6)
    vector_candidate_k = _safe_int_env("RAG_VECTOR_CANDIDATE_K", max(40, final_top_k * 10))
    bm25_candidate_k = _safe_int_env("RAG_BM25_CANDIDATE_K", max(40, final_top_k * 10))
    fusion_candidate_k = _safe_int_env("RAG_FUSION_CANDIDATE_K", max(30, final_top_k * 8))
    rerank_top_n = _safe_int_env("RAG_RERANK_TOP_N", max(20, final_top_k * 4))
    schema = (os.getenv("PGVECTOR_SCHEMA") or "supportportal").strip() or "supportportal"
    raw_table = (os.getenv("PGVECTOR_TABLE") or DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE
    table_name = raw_table if "." in raw_table else f"{schema}.{raw_table}"
    context_window = _safe_int_env("RAG_CONTEXT_WINDOW_TOKENS", model_context_window(answer_profile.model))
    embedding_provider = embedding_provider_name()
    embedding_model = embedding_model_id()
    rerank_provider = (os.getenv("RAG_RERANK_PROVIDER") or "siliconflow").strip() or "siliconflow"
    rerank_model = (os.getenv("RAG_RERANK_MODEL") or "BAAI/bge-reranker-v2-m3").strip() or "BAAI/bge-reranker-v2-m3"
    rerank_api_key = (
        (os.getenv("RAG_RERANK_API_KEY") or "").strip()
        or _siliconflow_api_key()
    )
    rerank_base_url = (
        (os.getenv("RAG_RERANK_BASE_URL") or "").strip()
        or (os.getenv("SILICONFLOW_BASE_URL") or "https://api.siliconflow.cn/v1").strip()
    )
    vector_enabled = _embedding_capability_enabled(embedding_provider)
    rerank_enabled = _rerank_capability_enabled(
        provider=rerank_provider,
        api_key=rerank_api_key,
        base_url=rerank_base_url,
        model=rerank_model,
    )
    return {
        "dsn": dsn,
        "app_schema": schema,
        "table": table_name,
        "top_k": final_top_k,
        "vector_candidate_k": vector_candidate_k,
        "bm25_candidate_k": bm25_candidate_k,
        "fts_candidate_k": bm25_candidate_k,
        "keyword_candidate_k": bm25_candidate_k,
        "fusion_candidate_k": fusion_candidate_k,
        "rerank_top_n": rerank_top_n,
        "bm25_k1": _safe_float_env("RAG_BM25_K1", 1.2),
        "bm25_b": _safe_float_env("RAG_BM25_B", 0.75),
        "bm25_max_query_terms": _safe_int_env("RAG_BM25_MAX_QUERY_TERMS", 6),
        "bm25_max_term_doc_freq_ratio": _safe_float_env("RAG_BM25_MAX_TERM_DOC_FREQ_RATIO", 0.08),
        "api_key": answer_profile.api_key,
        "chat_model": answer_profile.model,
        "reasoning_effort": answer_profile.reasoning_effort,
        "fallback_models": list(answer_profile.fallback_models),
        "context_budget_enabled": _feature_flag_enabled("RAG_CONTEXT_BUDGET_ENABLED", True),
        "context_window": context_window,
        "reserved_output_tokens": _safe_int_env("RAG_CONTEXT_OUTPUT_RESERVE_TOKENS", 1200),
        "buffer_tokens": _safe_int_env("RAG_CONTEXT_BUFFER_TOKENS", 1200),
        "context_compression_enabled": _feature_flag_enabled("RAG_CONTEXT_COMPRESSION_ENABLED", True),
        "context_compression_model": compression_profile.model,
        "context_compression_reasoning_effort": compression_profile.reasoning_effort,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "vector_enabled": vector_enabled,
        "rerank_provider": rerank_provider,
        "rerank_model": rerank_model,
        "rerank_api_key": rerank_api_key,
        "rerank_base_url": rerank_base_url,
        "rerank_enabled": rerank_enabled,
        "rerank_timeout_seconds": _safe_float_env("RAG_RERANK_TIMEOUT_SECONDS", 10.0),
        "rerank_max_retries": _safe_int_env("RAG_RERANK_MAX_RETRIES", 1),
        "request_timeout_seconds": answer_profile.timeout_seconds,
        "max_retries": answer_profile.max_retries,
    }


def _apply_light_path_latency_budget(config: dict[str, Any]) -> dict[str, Any]:
    adjusted = dict(config)
    adjusted["bm25_candidate_k"] = min(
        max(1, int(adjusted.get("bm25_candidate_k") or _LIGHT_PATH_BM25_CANDIDATE_K)),
        _LIGHT_PATH_BM25_CANDIDATE_K,
    )
    adjusted["fts_candidate_k"] = min(
        max(1, int(adjusted.get("fts_candidate_k") or adjusted.get("keyword_candidate_k") or _LIGHT_PATH_FTS_CANDIDATE_K)),
        _LIGHT_PATH_FTS_CANDIDATE_K,
    )
    adjusted["fusion_candidate_k"] = min(
        max(1, int(adjusted.get("fusion_candidate_k") or _LIGHT_PATH_FUSION_CANDIDATE_K)),
        _LIGHT_PATH_FUSION_CANDIDATE_K,
    )
    adjusted["rerank_top_n"] = min(
        max(1, int(adjusted.get("rerank_top_n") or _LIGHT_PATH_RERANK_TOP_N)),
        _LIGHT_PATH_RERANK_TOP_N,
    )
    adjusted["light_path_generation_chunk_limit"] = min(
        max(1, int(adjusted.get("top_k") or 1)),
        _LIGHT_PATH_CONTEXT_CHUNK_LIMIT,
    )
    return adjusted


def _list_vector_tables_with_primary_counts(dsn: str, schema: str) -> list[tuple[str, int]]:
    resolved_dsn = str(dsn or "").strip()
    resolved_schema = str(schema or "").strip() or "supportportal"
    if not resolved_dsn:
        return []

    psycopg = _import_psycopg()
    sql = psycopg.sql
    count_query_template = sql.SQL(
        """
        SELECT count(*) FILTER (WHERE index_role = 'primary')
        FROM {}
        """
    )

    counts: list[tuple[str, int]] = []
    with psycopg.connect(resolved_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_type = 'BASE TABLE'
                  AND table_name LIKE 'docagent_chunks%%'
                ORDER BY table_name
                """,
                (resolved_schema,),
            )
            rows = cur.fetchall()

            for row in rows:
                table_name = str(row[0] or "").strip()
                if not table_name:
                    continue
                try:
                    cur.execute(
                        count_query_template.format(sql.Identifier(resolved_schema, table_name))
                    )
                    count_row = cur.fetchone()
                except Exception:
                    continue
                primary_count = int(count_row[0] or 0) if count_row else 0
                counts.append((f"{resolved_schema}.{table_name}", primary_count))

    return sorted(counts, key=lambda item: (item[1], item[0]), reverse=True)


def _count_primary_rows_in_table(dsn: str, raw_table: str) -> int | None:
    resolved_dsn = str(dsn or "").strip()
    resolved_table = str(raw_table or "").strip()
    if not resolved_dsn or not resolved_table:
        return None

    psycopg = _import_psycopg()
    sql = psycopg.sql
    schema, table_name = _split_table_name(resolved_table)
    query = sql.SQL(
        """
        SELECT count(*) FILTER (WHERE index_role = 'primary')
        FROM {}
        """
    ).format(sql.Identifier(schema, table_name))

    with psycopg.connect(resolved_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def _resolve_active_vector_table(config: dict[str, Any]) -> str:
    cached_table = _cached_active_vector_table(config)
    if cached_table is not None:
        return cached_table

    configured_table = str(config.get("table") or "").strip()
    configured_dsn = str(config.get("dsn") or "").strip()
    schema, _ = _split_table_name(configured_table)
    if not configured_table or not configured_dsn:
        return configured_table

    try:
        configured_count = _count_primary_rows_in_table(configured_dsn, configured_table)
    except Exception as exc:
        logger.warning(
            "RAG configured vector table probe failed for %s: %s",
            configured_table,
            exc,
        )
        configured_count = None
    if configured_count is not None and configured_count > 0:
        _store_active_vector_table_cache(config, configured_table)
        return configured_table

    try:
        candidate_tables = _list_vector_tables_with_primary_counts(configured_dsn, schema)
    except Exception as exc:
        logger.warning(
            "RAG vector table discovery failed for %s: %s",
            configured_table,
            exc,
        )
        return configured_table

    configured_count = next(
        (count for table_name, count in candidate_tables if table_name == configured_table),
        None,
    )
    if configured_count is not None and configured_count > 0:
        _store_active_vector_table_cache(config, configured_table)
        return configured_table

    fallback_table = next(
        (table_name for table_name, count in candidate_tables if count > 0),
        None,
    )
    if fallback_table and fallback_table != configured_table:
        logger.warning(
            "Configured RAG vector table %s has no primary rows. Falling back to %s.",
            configured_table,
            fallback_table,
        )
        _store_active_vector_table_cache(config, fallback_table)
        return fallback_table

    _store_active_vector_table_cache(config, configured_table)
    return configured_table


def _table_identifier(sql: Any, raw_table: str) -> Any:
    schema, table_name = _split_table_name(raw_table)
    return sql.Identifier(schema, table_name)


def _app_table_identifier(sql: Any, schema: str, table_name: str) -> Any:
    return sql.Identifier(schema, table_name)


def _select_bm25_query_terms(
    *,
    terms: list[str],
    term_doc_freqs: dict[str, int],
    doc_count: int,
    max_term_doc_freq_ratio: float,
    max_query_terms: int,
) -> list[str]:
    normalized_terms = [
        str(term or "").strip().lower()
        for term in terms
        if str(term or "").strip() and not is_bm25_query_stopword(str(term or "").strip().lower())
    ]
    if not normalized_terms:
        return []
    safe_doc_count = max(0, int(doc_count or 0))
    safe_max_query_terms = max(1, int(max_query_terms or 1))
    safe_ratio = max(0.0, float(max_term_doc_freq_ratio or 0.0))

    def _rank_key(term: str) -> tuple[int, int, str]:
        doc_freq = max(0, int(term_doc_freqs.get(term) or 0))
        return (doc_freq if doc_freq > 0 else safe_doc_count + 1, -len(term), term)

    filtered_terms: list[str] = []
    for term in normalized_terms:
        doc_freq = max(0, int(term_doc_freqs.get(term) or 0))
        if safe_doc_count > 0 and doc_freq > 0 and (doc_freq / safe_doc_count) > safe_ratio:
            continue
        filtered_terms.append(term)

    ranked_terms = sorted(filtered_terms or normalized_terms, key=_rank_key)
    selected: list[str] = []
    seen: set[str] = set()
    for term in ranked_terms:
        if term in seen:
            continue
        seen.add(term)
        selected.append(term)
        if len(selected) >= safe_max_query_terms:
            break
    return selected


def _retrieve_chunks(
    message: str,
    config: dict[str, Any],
    *,
    limit: int | None = None,
    index_role: str = "primary",
) -> list[RetrievedChunk]:
    psycopg = _import_psycopg()
    sql = psycopg.sql
    retrieval_plan = _config_retrieval_plan(config)
    downpush_filters = downpush_hard_filters(retrieval_plan) if retrieval_plan is not None else {}
    filter_sql, filter_params = _metadata_filter_clauses(sql, downpush_filters, metadata_ref="metadata")

    provider = get_embedding_provider()
    query_embedding = provider.embed_query(message)
    vector_param = _vector_literal(query_embedding)
    normalized_index_role = str(index_role or "").strip().lower() or "primary"

    query = sql.SQL(
        """
        SELECT
            id,
            doc_id,
            content,
            source_path,
            h1,
            h2,
            h3,
            source_url,
            metadata,
            metadata ->> 'source_type' AS source_type,
            chunk_strategy,
            1 - (embedding <=> %s::vector) AS similarity
        FROM {}
        WHERE index_role = %s
        {}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """
    ).format(_table_identifier(sql, config["table"]), filter_sql)

    with psycopg.connect(config["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (vector_param, normalized_index_role, *filter_params, vector_param, int(limit or config["top_k"])),
            )
            rows = cur.fetchall()

    chunks: list[RetrievedChunk] = []
    for row in rows:
        chunks.append(
            RetrievedChunk(
                chunk_id=str(row[0]),
                doc_id=(str(row[1]).strip() or None) if row[1] is not None else None,
                text=str(row[2]),
                source_path=str(row[3]),
                h1=(str(row[4]).strip() or None) if row[4] is not None else None,
                h2=(str(row[5]).strip() or None) if row[5] is not None else None,
                h3=(str(row[6]).strip() or None) if row[6] is not None else None,
                source_url=(str(row[7]).strip() or None) if row[7] is not None else None,
                metadata=row[8] if isinstance(row[8], dict) else {},
                source_type=(str(row[9]).strip() or None) if row[9] is not None else None,
                chunk_strategy=(str(row[10]).strip() or None) if row[10] is not None else None,
                similarity=float(row[11]) if row[11] is not None else 0.0,
                index_role=normalized_index_role,
                retrieval_sources=["vector"],
                candidate_trace={
                    "vector_similarity": float(row[11]) if row[11] is not None else 0.0,
                    "raw_score": float(row[11]) if row[11] is not None else 0.0,
                    "index_role": normalized_index_role,
                },
            )
        )
    return chunks


def _retrieve_bm25_chunks(
    message: str,
    config: dict[str, Any],
    *,
    limit: int | None = None,
    index_role: str = "primary",
) -> list[RetrievedChunk]:
    terms = tokenize_bm25_query(message)
    if not terms:
        return []

    psycopg = _import_psycopg()
    sql = psycopg.sql
    retrieval_plan = _config_retrieval_plan(config)
    downpush_filters = downpush_hard_filters(retrieval_plan) if retrieval_plan is not None else {}
    filter_sql, filter_params = _metadata_filter_clauses(sql, downpush_filters, metadata_ref="v.metadata")
    app_schema = str(config.get("app_schema") or "supportportal").strip() or "supportportal"
    normalized_index_role = str(index_role or "").strip().lower() or "primary"
    query = sql.SQL(
        """
        WITH query_terms AS (
            SELECT
                q.term,
                t.doc_freq
            FROM unnest(%s::text[]) AS q(term)
            JOIN {} AS t
              ON t.term = q.term
             AND t.index_role = %s
        ),
        stats AS (
            SELECT doc_count, avg_doc_length
            FROM {}
            WHERE index_role = %s
        ),
        matched_postings AS MATERIALIZED (
            SELECT
                p.chunk_id,
                p.tf,
                q.doc_freq
            FROM query_terms AS q
            JOIN {} AS p
              ON p.term = q.term
             AND p.index_role = %s
        ),
        matched_docs AS MATERIALIZED (
            SELECT
                d.chunk_id,
                d.doc_length
            FROM {} AS d
            JOIN (
                SELECT DISTINCT chunk_id FROM matched_postings
            ) AS matched
              ON matched.chunk_id = d.chunk_id
            WHERE d.index_role = %s
        ),
        scored AS (
            SELECT
                p.chunk_id,
                SUM(
                    LN(
                        1.0::double precision
                        + (
                            (
                                ((stats.doc_count - p.doc_freq)::double precision + 0.5::double precision)
                                /
                                ((p.doc_freq)::double precision + 0.5::double precision)
                            )
                        )
                    ) *
                    (
                        ((p.tf)::double precision * (%s::double precision + 1.0::double precision))
                        /
                        (
                            (p.tf)::double precision
                            + (
                                %s::double precision
                                * (
                                    1.0::double precision
                                    - %s::double precision
                                    + (
                                        %s::double precision
                                        * (
                                            (d.doc_length)::double precision
                                            / NULLIF(stats.avg_doc_length, 0.0::double precision)
                                        )
                                    )
                                )
                            )
                        )
                    )
                ) AS bm25_score
            FROM matched_postings AS p
            JOIN matched_docs AS d
              ON d.chunk_id = p.chunk_id
            CROSS JOIN stats
            GROUP BY p.chunk_id
        )
        SELECT
            v.id,
            v.doc_id,
            v.content,
            v.source_path,
            v.h1,
            v.h2,
            v.h3,
            v.source_url,
            v.metadata,
            v.metadata ->> 'source_type' AS source_type,
            v.chunk_strategy,
            scored.bm25_score
        FROM scored
        JOIN {} AS v
          ON v.id = scored.chunk_id
        WHERE v.index_role = %s
          {}
        ORDER BY scored.bm25_score DESC, v.updated_at DESC
        LIMIT %s
        """
    ).format(
        _app_table_identifier(sql, app_schema, "support_knowledge_bm25_terms"),
        _app_table_identifier(sql, app_schema, "support_knowledge_bm25_stats"),
        _app_table_identifier(sql, app_schema, "support_knowledge_bm25_postings"),
        _app_table_identifier(sql, app_schema, "support_knowledge_bm25_docs"),
        _table_identifier(sql, config["table"]),
        filter_sql,
    )

    with psycopg.connect(config["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT term, doc_freq
                    FROM {}
                    WHERE index_role = %s
                      AND term = ANY(%s)
                    """
                ).format(_app_table_identifier(sql, app_schema, "support_knowledge_bm25_terms")),
                (normalized_index_role, terms),
            )
            term_doc_freqs = {
                str(row[0]).strip().lower(): int(row[1] or 0)
                for row in (cur.fetchall() or [])
                if len(row) >= 2 and str(row[0]).strip()
            }
            cur.execute(
                sql.SQL(
                    """
                    SELECT doc_count
                    FROM {}
                    WHERE index_role = %s
                    """
                ).format(_app_table_identifier(sql, app_schema, "support_knowledge_bm25_stats"))
                ,
                (normalized_index_role,),
            )
            stats_row = cur.fetchone() or (0,)
            selected_terms = _select_bm25_query_terms(
                terms=terms,
                term_doc_freqs=term_doc_freqs,
                doc_count=int(stats_row[0] or 0),
                max_term_doc_freq_ratio=float(config["bm25_max_term_doc_freq_ratio"]),
                max_query_terms=int(config["bm25_max_query_terms"]),
            )
            if not selected_terms:
                return []
            cur.execute(
                query,
                (
                    selected_terms,
                    normalized_index_role,
                    normalized_index_role,
                    normalized_index_role,
                    normalized_index_role,
                    float(config["bm25_k1"]),
                    float(config["bm25_k1"]),
                    float(config["bm25_b"]),
                    float(config["bm25_b"]),
                    normalized_index_role,
                    *filter_params,
                    int(limit or config["bm25_candidate_k"]),
                ),
            )
            rows = cur.fetchall()

    raw_scores = [float(row[11]) for row in rows if row[11] is not None]
    max_score = max(raw_scores) if raw_scores else 0.0
    chunks: list[RetrievedChunk] = []
    for row in rows:
        raw_score = float(row[11]) if row[11] is not None else 0.0
        normalized_score = (raw_score / max_score) if max_score > 0 else 0.0
        chunks.append(
            RetrievedChunk(
                chunk_id=str(row[0]),
                doc_id=(str(row[1]).strip() or None) if row[1] is not None else None,
                text=str(row[2]),
                source_path=str(row[3]),
                h1=(str(row[4]).strip() or None) if row[4] is not None else None,
                h2=(str(row[5]).strip() or None) if row[5] is not None else None,
                h3=(str(row[6]).strip() or None) if row[6] is not None else None,
                source_url=(str(row[7]).strip() or None) if row[7] is not None else None,
                metadata=row[8] if isinstance(row[8], dict) else {},
                source_type=(str(row[9]).strip() or None) if row[9] is not None else None,
                chunk_strategy=(str(row[10]).strip() or None) if row[10] is not None else None,
                similarity=max(0.0, min(1.0, normalized_score)),
                index_role=normalized_index_role,
                retrieval_sources=["bm25"],
                candidate_trace={
                    "bm25_score": raw_score,
                    "raw_score": raw_score,
                    "index_role": normalized_index_role,
                },
            )
        )
    return chunks


def _retrieve_fts_chunks(
    message: str,
    config: dict[str, Any],
    *,
    limit: int | None = None,
    index_role: str = "primary",
) -> list[RetrievedChunk]:
    psycopg = _import_psycopg()
    sql = psycopg.sql
    normalized_index_role = str(index_role or "").strip().lower() or "primary"

    query = sql.SQL(
        """
        SELECT
            id,
            doc_id,
            content,
            source_path,
            h1,
            h2,
            h3,
            source_url,
            metadata,
            metadata ->> 'source_type' AS source_type,
            chunk_strategy,
            ts_rank_cd(
                to_tsvector(
                    'simple',
                    coalesce(h1, '')
                    || ' '
                    || coalesce(h2, '')
                    || ' '
                    || coalesce(h3, '')
                    || ' '
                    || coalesce(content, '')
                ),
                plainto_tsquery('simple', %s)
            ) AS rank
        FROM {}
        WHERE index_role = %s
          AND to_tsvector(
                'simple',
                coalesce(h1, '')
                || ' '
                || coalesce(h2, '')
                || ' '
                || coalesce(h3, '')
                || ' '
                || coalesce(content, '')
            ) @@ plainto_tsquery('simple', %s)
        ORDER BY rank DESC
        LIMIT %s
        """
    ).format(_table_identifier(sql, config["table"]))

    with psycopg.connect(config["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (message, normalized_index_role, message, int(limit or config["keyword_candidate_k"])))
            rows = cur.fetchall()

    chunks: list[RetrievedChunk] = []
    for row in rows:
        rank = float(row[11]) if row[11] is not None else 0.0
        chunks.append(
            RetrievedChunk(
                chunk_id=str(row[0]),
                doc_id=(str(row[1]).strip() or None) if row[1] is not None else None,
                text=str(row[2]),
                source_path=str(row[3]),
                h1=(str(row[4]).strip() or None) if row[4] is not None else None,
                h2=(str(row[5]).strip() or None) if row[5] is not None else None,
                h3=(str(row[6]).strip() or None) if row[6] is not None else None,
                source_url=(str(row[7]).strip() or None) if row[7] is not None else None,
                metadata=row[8] if isinstance(row[8], dict) else {},
                source_type=(str(row[9]).strip() or None) if row[9] is not None else None,
                chunk_strategy=(str(row[10]).strip() or None) if row[10] is not None else None,
                similarity=max(0.0, min(1.0, rank)),
                index_role=normalized_index_role,
                retrieval_sources=["fts"],
                candidate_trace={
                    "fts_rank": rank,
                    "raw_score": rank,
                    "index_role": normalized_index_role,
                },
            )
        )
    return chunks


def _extract_query_terms(message: str, max_terms: int = 6) -> list[str]:
    terms: list[str] = []
    for raw in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", message.lower()):
        term = raw.strip("_-")
        if not term or term in _QUERY_STOPWORDS:
            continue
        if term in terms:
            continue
        terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


def _chunk_search_text(chunk: RetrievedChunk) -> str:
    parts = [chunk.h1, chunk.h2, chunk.h3, chunk.text]
    return " ".join(str(part).lower() for part in parts if part)


def _keyword_hit_count(search_text: str, terms: list[str]) -> int:
    return sum(1 for term in terms if term in search_text)


def _retrieve_keyword_chunks(
    message: str,
    config: dict[str, Any],
    *,
    limit: int | None = None,
    index_role: str = "primary",
) -> list[RetrievedChunk]:
    terms = _extract_query_terms(message)
    if not terms:
        return []

    psycopg = _import_psycopg()
    sql = psycopg.sql
    retrieval_plan = _config_retrieval_plan(config)
    downpush_filters = downpush_hard_filters(retrieval_plan) if retrieval_plan is not None else {}
    filter_sql, filter_params = _metadata_filter_clauses(sql, downpush_filters, metadata_ref="metadata")
    patterns = [f"%{term}%" for term in terms]
    candidate_limit = max(int(config["top_k"]) * 25, 50)
    normalized_index_role = str(index_role or "").strip().lower() or "primary"

    query = sql.SQL(
        """
        SELECT
            id,
            doc_id,
            content,
            source_path,
            h1,
            h2,
            h3,
            source_url,
            metadata,
            metadata ->> 'source_type' AS source_type,
            chunk_strategy
        FROM {}
        WHERE
            index_role = %s
            AND (
            lower(content) LIKE ANY(%s)
            OR lower(coalesce(h1, '')) LIKE ANY(%s)
            OR lower(coalesce(h2, '')) LIKE ANY(%s)
            OR lower(coalesce(h3, '')) LIKE ANY(%s)
            )
            {}
        LIMIT %s
        """
    ).format(_table_identifier(sql, config["table"]), filter_sql)

    with psycopg.connect(config["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (normalized_index_role, patterns, patterns, patterns, patterns, *filter_params, candidate_limit),
            )
            rows = cur.fetchall()

    scored_chunks: list[tuple[int, RetrievedChunk]] = []
    for row in rows:
        chunk = RetrievedChunk(
            chunk_id=str(row[0]),
            doc_id=(str(row[1]).strip() or None) if row[1] is not None else None,
            text=str(row[2]),
            source_path=str(row[3]),
            h1=(str(row[4]).strip() or None) if row[4] is not None else None,
            h2=(str(row[5]).strip() or None) if row[5] is not None else None,
            h3=(str(row[6]).strip() or None) if row[6] is not None else None,
            source_url=(str(row[7]).strip() or None) if row[7] is not None else None,
            metadata=row[8] if isinstance(row[8], dict) else {},
            source_type=(str(row[9]).strip() or None) if row[9] is not None else None,
            chunk_strategy=(str(row[10]).strip() or None) if row[10] is not None else None,
            similarity=0.0,
            index_role=normalized_index_role,
        )
        hits = _keyword_hit_count(_chunk_search_text(chunk), terms)
        if hits <= 0:
            continue
        chunk.similarity = min(1.0, hits / max(1, len(terms)))
        chunk.retrieval_sources = ["keyword_fallback"]
        chunk.candidate_trace = {
            "keyword_fallback_hits": hits,
            "raw_score": hits,
            "index_role": normalized_index_role,
        }
        scored_chunks.append((hits, chunk))

    scored_chunks.sort(key=lambda item: (item[0], item[1].similarity), reverse=True)
    top_k = int(limit or config["top_k"])
    results: list[RetrievedChunk] = []
    seen_keys: set[str] = set()
    for _, chunk in scored_chunks:
        dedupe_key = chunk.chunk_id or f"{chunk.source_path}:{chunk.text[:120]}"
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        results.append(chunk)
        if len(results) >= top_k:
            break
    return results


def _rrf_merge(
    vector_chunks: list[RetrievedChunk],
    bm25_chunks: list[RetrievedChunk],
    *,
    limit: int,
) -> list[RetrievedChunk]:
    rrf_k = 60.0
    merged_scores: dict[str, float] = {}
    merged_chunks: dict[str, RetrievedChunk] = {}

    for ranked_chunks in [vector_chunks, bm25_chunks]:
        for index, chunk in enumerate(ranked_chunks, start=1):
            dedupe_key = chunk.chunk_id or f"{chunk.source_path}:{chunk.text[:120]}"
            merged_scores[dedupe_key] = merged_scores.get(dedupe_key, 0.0) + (1.0 / (rrf_k + index))
            existing = merged_chunks.get(dedupe_key)
            if existing is None or chunk.similarity > existing.similarity:
                merged_chunks[dedupe_key] = chunk
            merged_chunk = merged_chunks[dedupe_key]
            if ranked_chunks is vector_chunks:
                merged_chunk.candidate_trace["vector_rank"] = index
                merged_chunk.candidate_trace["vector_similarity"] = float(chunk.similarity or 0.0)
                if "vector" not in merged_chunk.retrieval_sources:
                    merged_chunk.retrieval_sources.append("vector")
            else:
                merged_chunk.candidate_trace["bm25_rank"] = index
                merged_chunk.candidate_trace["bm25_score"] = chunk.candidate_trace.get("bm25_score")
                if "bm25" not in merged_chunk.retrieval_sources:
                    merged_chunk.retrieval_sources.append("bm25")

    ordered = sorted(
        merged_scores.items(),
        key=lambda item: (item[1], merged_chunks[item[0]].similarity),
        reverse=True,
    )
    results: list[RetrievedChunk] = []
    for rank, (dedupe_key, score) in enumerate(ordered[: max(1, int(limit))], start=1):
        chunk = merged_chunks[dedupe_key]
        chunk.candidate_trace["rrf_rank"] = rank
        chunk.candidate_trace["rrf_score"] = round(float(score), 6)
        results.append(chunk)
    return results


def _merge_chunks(
    primary_chunks: list[RetrievedChunk],
    secondary_chunks: list[RetrievedChunk],
    limit: int,
) -> list[RetrievedChunk]:
    merged: list[RetrievedChunk] = []
    seen_keys: set[str] = set()
    for chunk in [*primary_chunks, *secondary_chunks]:
        dedupe_key = chunk.chunk_id or f"{chunk.source_path}:{chunk.text[:120]}"
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        merged.append(chunk)
        if len(merged) >= limit:
            break
    return merged


def _has_grounded_keyword_overlap(message: str, chunks: list[RetrievedChunk]) -> bool:
    terms = _extract_query_terms(message)
    if not terms or not chunks:
        return False

    min_hits = 1 if len(terms) == 1 else 2
    for chunk in chunks[:5]:
        if _keyword_hit_count(_chunk_search_text(chunk), terms) >= min_hits:
            return True
    return False


def _format_context(chunks: list[RetrievedChunk]) -> str:
    blocks: list[str] = []
    for chunk in chunks:
        blocks.append(
            f"[{chunk.chunk_id}] {chunk.source_path} | {_build_heading(chunk)}\n"
            f"{chunk.text.strip()}"
        )
    return "\n\n---\n\n".join(blocks)


def _chunk_map_by_id(chunks: list[RetrievedChunk]) -> dict[str, RetrievedChunk]:
    return {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}


def _build_answer_prompt(question: str, context_block: str) -> str:
    return _build_answer_prompt_for_mode(question, context_block, repair_mode=False)


def _build_answer_prompt_for_mode(question: str, context_block: str, *, repair_mode: bool) -> str:
    return build_rag_answer_user_prompt(
        question=question,
        context_block=context_block,
        insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
        repair_mode=repair_mode,
    )


def _response_to_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")).strip())
            else:
                parts.append(str(item).strip())
        return "\n".join([part for part in parts if part]).strip()
    return str(content).strip()


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    content = text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        if content.endswith("```"):
            content = content[:-3].strip()

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_valid_response(payload: dict[str, Any], allowed_chunk_ids: set[str]) -> bool:
    if not isinstance(payload.get("answer"), str):
        return False
    if not isinstance(payload.get("key_steps"), list):
        return False
    if not isinstance(payload.get("citations"), list):
        return False
    if not isinstance(payload.get("insufficient_evidence"), bool):
        return False

    for item in payload["key_steps"]:
        if not isinstance(item, str):
            return False
    for citation in payload["citations"]:
        if not isinstance(citation, str) or citation not in allowed_chunk_ids:
            return False
    if payload["insufficient_evidence"] is False and len(payload["citations"]) == 0:
        return False
    return True


def _build_answer_text(answer: str, key_steps: list[str]) -> str:
    cleaned_steps = [step.strip() for step in key_steps if isinstance(step, str) and step.strip()]
    if not cleaned_steps:
        return answer.strip()
    lines = [answer.strip(), "", "Key Steps:"]
    for index, step in enumerate(cleaned_steps, start=1):
        lines.append(f"{index}. {step}")
    return "\n".join(lines).strip()


def _build_extractive_fallback(chunks: list[RetrievedChunk]) -> str:
    lines = [
        "I found relevant support evidence, but I could not verify a complete grounded answer.",
        "",
        "Evidence:",
    ]
    for index, chunk in enumerate(chunks[:2], start=1):
        snippet = " ".join(chunk.text.split())
        heading = _build_heading(chunk)
        lines.append(f"{index}. {heading}: {snippet[:160]}")
    return "\n".join(lines)


def _citation_records_from_ids(
    citation_ids: list[str],
    chunks: list[RetrievedChunk],
) -> list[dict[str, str]]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    records: list[dict[str, str]] = []
    for chunk_id in citation_ids:
        chunk = chunk_map.get(chunk_id)
        if chunk is None:
            continue
        record: dict[str, str] = {
            "chunk_id": chunk.chunk_id,
            "source_path": chunk.source_path,
            "heading": _build_heading(chunk),
        }
        if chunk.source_url:
            record["source_url"] = chunk.source_url
        records.append(record)
    return records


def _citation_records_from_chunks(chunks: list[RetrievedChunk], limit: int = 3) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for chunk in chunks[:limit]:
        record: dict[str, str] = {
            "chunk_id": chunk.chunk_id,
            "source_path": chunk.source_path,
            "heading": _build_heading(chunk),
        }
        if chunk.source_url:
            record["source_url"] = chunk.source_url
        records.append(record)
    return records


def _build_extractive_rag_answer(chunks: list[RetrievedChunk]) -> RagAnswer:
    sources: list[str] = [f"rag:{chunk.chunk_id}" for chunk in chunks[:2] if chunk.chunk_id]
    if not sources:
        sources = [f"rag:{chunk.source_path}" for chunk in chunks[:2] if chunk.source_path]
    citations = _citation_records_from_chunks(chunks, limit=2)
    url_sources = [record["source_url"] for record in citations if record.get("source_url")]
    if url_sources:
        sources = url_sources
    return RagAnswer(
        answer=_build_extractive_fallback(chunks),
        confidence=_confidence_from_chunks(chunks),
        sources=sources or ["rag"],
        citations=citations,
    )


def _build_answer_profile(
    config: dict[str, Any],
    *,
    use_light_path_fast_model: bool = False,
) -> ModelProfile:
    defaults = resolve_model_profile(RAG_ANSWER_SCENARIO)
    model_name = str(config.get("chat_model") or "").strip() or defaults.model
    reasoning_effort = str(config.get("reasoning_effort") or "").strip() or defaults.reasoning_effort or "high"
    fallback_models = tuple(config.get("fallback_models") or defaults.fallback_models)
    if use_light_path_fast_model:
        model_name = _LIGHT_PATH_FAST_ANSWER_MODEL
        reasoning_effort = _LIGHT_PATH_FAST_ANSWER_REASONING_EFFORT
        fallback_models = ()
    return ModelProfile(
        scenario=RAG_ANSWER_SCENARIO,
        provider="openai",
        model=model_name,
        api_mode="openai_responses",
        api_key=str(config.get("api_key") or "").strip() or defaults.api_key,
        reasoning_effort=reasoning_effort,
        temperature=0.0,
        timeout_seconds=float(config.get("request_timeout_seconds") or defaults.timeout_seconds),
        max_retries=int(config.get("max_retries") or defaults.max_retries),
        fallback_models=fallback_models,
    )


def _invoke_llm_payload(
    message: str,
    chunks: list[RetrievedChunk],
    config: dict[str, Any],
    strict_retry: bool = False,
    *,
    packed_evidence: PackedEvidence | None = None,
    product: str | None = None,
    profile_override: ModelProfile | None = None,
) -> dict[str, Any] | None:
    context_block = packed_evidence.prompt_context if packed_evidence is not None else _format_context(chunks)
    prompt = _build_answer_prompt_for_mode(message, context_block, repair_mode=strict_retry)
    profile = profile_override or _build_answer_profile(config)
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=_build_answer_system_prompt(product),
            user_prompt=prompt,
        )
    except LlmInvocationError:
        return None
    return _extract_json_payload(response.text)


def _invoke_llm_payload_with_trace(
    message: str,
    chunks: list[RetrievedChunk],
    config: dict[str, Any],
    strict_retry: bool = False,
    *,
    packed_evidence: PackedEvidence | None = None,
    product: str | None = None,
    profile_override: ModelProfile | None = None,
) -> tuple[dict[str, Any] | None, int, int, str | None]:
    context_block = packed_evidence.prompt_context if packed_evidence is not None else _format_context(chunks)
    prompt = _build_answer_prompt_for_mode(message, context_block, repair_mode=strict_retry)
    profile = profile_override or _build_answer_profile(config)
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=_build_answer_system_prompt(product),
            user_prompt=prompt,
        )
    except LlmInvocationError:
        return None, 0, 0, None
    payload = _extract_json_payload(response.text)
    if payload is not None:
        return payload, response.prompt_tokens, response.completion_tokens, response.model_name
    return None, 0, 0, None


def _confidence_from_chunks(chunks: list[RetrievedChunk]) -> float:
    if not chunks:
        return 0.0
    best_similarity = max(0.0, min(1.0, chunks[0].similarity))
    confidence = 0.72 + (0.2 * best_similarity) + (0.02 * min(len(chunks), 5))
    return round(min(0.95, confidence), 2)


def _infer_query_type(message: str) -> str:
    text = str(message or "").strip().lower()
    if not text:
        return "unclear_query"
    if any(term in text for term in ["hello", "hi ", "thanks", "thank you"]):
        return "small_talk"
    if "error code" in text or re.search(r"\b\d{3,5}\b", text):
        return "error_code"
    if any(term in text for term in ["price", "pricing", "policy", "plan", "billing"]):
        return "pricing_or_policy"
    if any(term in text for term in ["configure", "configuration", "setup", "enable", "disable"]):
        return "configuration"
    if any(term in text for term in ["troubleshoot", "issue", "problem", "delay", "missing", "failed", "failure", "root cause"]):
        return "troubleshooting"
    if any(term in text for term in ["what", "how", "where", "can i", "does", "is there"]):
        return "faq"
    return "unclear_query"


def _dominant_value(chunks: list[RetrievedChunk], attr_name: str) -> str | None:
    counts: dict[str, int] = {}
    for chunk in chunks:
        value = str(getattr(chunk, attr_name, "") or "").strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _unique_doc_ids(chunks: list[RetrievedChunk]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        doc_id = str(chunk.doc_id or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        values.append(doc_id)
    return values


def _chunk_family_key(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    product = (
        str(metadata.get("product") or "").strip().lower()
        or str(metadata.get("product_area") or "").strip().lower()
    )
    source_family = str(metadata.get("source_family") or "").strip().replace("\\", "/").strip("/").lower()
    source_path = str(chunk.source_path or "").strip().replace("\\", "/")
    source_stem = os.path.splitext(os.path.basename(source_path))[0].strip().lower()
    doc_id = str(chunk.doc_id or "").strip().lower()
    family = source_family or source_stem or doc_id
    if not family:
        return ""
    return f"{product}::{family}" if product else family


def _chunk_section_key(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    section_path = " > ".join(_chunk_metadata_list(metadata.get("section_path"))).strip().lower()
    use_case = str(metadata.get("use_case") or "").strip().lower()
    chunk_type = str(metadata.get("chunk_type") or "").strip().lower()
    signature = " | ".join(part for part in [section_path, use_case, chunk_type] if part)
    return signature


def _chunk_method_name(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    return str(metadata.get("method_name") or "").strip()


def _chunk_selection_key(chunk: RetrievedChunk, index: int) -> str:
    if chunk.chunk_id:
        return chunk.chunk_id
    return f"{index}:{chunk.source_path}:{chunk.text[:120]}"


def _select_diverse_chunks(chunks: list[RetrievedChunk], *, limit: int, query: str | None = None) -> list[RetrievedChunk]:
    safe_limit = max(1, int(limit or 1))
    if not chunks:
        return []

    selected: list[RetrievedChunk] = []
    selected_chunk_keys: set[str] = set()
    selected_families: set[str] = set()
    selected_sections: set[str] = set()
    mentioned_methods = _mentioned_method_names(query or "")
    comparison_mode = _is_method_comparison_query(query or "", mentioned_methods)

    def _select_chunk(chunk: RetrievedChunk, index: int) -> None:
        chunk_key = _chunk_selection_key(chunk, index)
        family_key = _chunk_family_key(chunk)
        section_key = _chunk_section_key(chunk)
        selected.append(chunk)
        selected_chunk_keys.add(chunk_key)
        if family_key:
            selected_families.add(family_key)
        if section_key:
            selected_sections.add(section_key)

    if comparison_mode:
        for method_name in mentioned_methods:
            for index, chunk in enumerate(chunks):
                chunk_key = _chunk_selection_key(chunk, index)
                if chunk_key in selected_chunk_keys:
                    continue
                if _chunk_method_name(chunk) != method_name:
                    continue
                _select_chunk(chunk, index)
                break
            if len(selected) >= safe_limit:
                return selected

    # Pass 1: keep the best-ranked chunk for each family.
    for index, chunk in enumerate(chunks):
        chunk_key = _chunk_selection_key(chunk, index)
        family_key = _chunk_family_key(chunk)
        if chunk_key in selected_chunk_keys:
            continue
        if family_key and family_key in selected_families:
            continue
        _select_chunk(chunk, index)
        if len(selected) >= safe_limit:
            return selected

    # Pass 2: prefer new sections/use-cases before backfilling repeated context.
    for index, chunk in enumerate(chunks):
        chunk_key = _chunk_selection_key(chunk, index)
        if chunk_key in selected_chunk_keys:
            continue
        section_key = _chunk_section_key(chunk)
        if not section_key or section_key in selected_sections:
            continue
        _select_chunk(chunk, index)
        if len(selected) >= safe_limit:
            return selected

    # Pass 3: if diversity signals are exhausted, backfill by the original order.
    for index, chunk in enumerate(chunks):
        chunk_key = _chunk_selection_key(chunk, index)
        if chunk_key in selected_chunk_keys:
            continue
        _select_chunk(chunk, index)
        if len(selected) >= safe_limit:
            break
    return selected


def _reorder_chunks_for_rerank(chunks: list[RetrievedChunk], *, limit: int, query: str | None = None) -> list[RetrievedChunk]:
    if not chunks:
        return []
    safe_limit = min(len(chunks), max(1, int(limit or 1)))
    prioritized = _select_diverse_chunks(chunks, limit=safe_limit, query=query) or list(chunks[:safe_limit])
    prioritized_objects = {id(chunk) for chunk in prioritized}
    ordered = list(prioritized)
    for chunk in chunks:
        if id(chunk) in prioritized_objects:
            continue
        ordered.append(chunk)
    return ordered


def _selected_contexts(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for chunk in chunks:
        contexts.append(
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "source_path": chunk.source_path,
                "heading": _build_heading(chunk),
                "source_url": chunk.source_url,
                "source_type": chunk.source_type,
                "chunk_strategy": chunk.chunk_strategy,
                "index_role": chunk.index_role,
                "similarity": round(max(0.0, min(1.0, float(chunk.similarity))), 4),
                "metadata": chunk.metadata if isinstance(chunk.metadata, dict) else {},
                "rerank_score": chunk.rerank_score,
                "rerank_reasons": list(chunk.rerank_reasons),
                "text": chunk.text,
            }
        )
    return contexts


def _candidate_rows(
    chunks: list[RetrievedChunk],
    reranked_chunks: list[RetrievedChunk],
    *,
    selected_chunk_ids: set[str],
) -> list[dict[str, Any]]:
    rerank_positions = {chunk.chunk_id: index for index, chunk in enumerate(reranked_chunks, start=1) if chunk.chunk_id}
    rerank_scores = {chunk.chunk_id: chunk.rerank_score for chunk in reranked_chunks if chunk.chunk_id}
    rerank_reasons = {chunk.chunk_id: list(chunk.rerank_reasons) for chunk in reranked_chunks if chunk.chunk_id}
    rows: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        candidate_trace = dict(chunk.candidate_trace) if isinstance(chunk.candidate_trace, dict) else {}
        candidate_trace["retrieval_sources"] = list(dict.fromkeys(chunk.retrieval_sources))
        candidate_trace["metadata_reasons"] = rerank_reasons.get(chunk.chunk_id, [])
        rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "rank_before_rerank": index,
                "rank_after_rerank": rerank_positions.get(chunk.chunk_id),
                "retrieval_score": round(max(0.0, min(1.0, float(chunk.similarity))), 4),
                "rerank_score": rerank_scores.get(chunk.chunk_id),
                "metadata_reasons": rerank_reasons.get(chunk.chunk_id, []),
                "title": _build_heading(chunk),
                "source_url": chunk.source_url,
                "index_role": chunk.index_role,
                "used_in_final_answer": chunk.chunk_id in selected_chunk_ids,
                "candidate_trace": candidate_trace,
            }
        )
    return rows


def _rerank_document_text(chunk: RetrievedChunk) -> str:
    return "\n".join(
        part
        for part in [
            chunk.source_path,
            _build_heading(chunk),
            chunk.text.strip(),
        ]
        if str(part or "").strip()
    ).strip()


def _rerank_chunks(
    query: str,
    chunks: list[RetrievedChunk],
    config: dict[str, Any],
    *,
    limit: int | None = None,
) -> list[RetrievedChunk]:
    if not chunks:
        return []
    provider = str(config.get("rerank_provider") or "").strip().lower()
    if provider != "siliconflow":
        return chunks
    api_key = str(config.get("rerank_api_key") or "").strip()
    base_url = str(config.get("rerank_base_url") or "").strip().rstrip("/")
    model = str(config.get("rerank_model") or "").strip()
    if not api_key or not base_url or not model:
        return chunks
    if not _runtime_capability_available("rerank", provider=provider):
        config["_rerank_runtime_available"] = False
        return chunks

    rerank_limit = min(len(chunks), max(1, int(limit or config.get("rerank_top_n") or len(chunks))))
    rerank_candidates = list(chunks[:rerank_limit])
    tail_chunks = list(chunks[rerank_limit:])
    payload = json.dumps(
        {
            "model": model,
            "query": query,
            "documents": [_rerank_document_text(chunk) for chunk in rerank_candidates],
            "top_n": rerank_limit,
            "return_documents": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url=f"{base_url}/rerank",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    max_retries = max(0, int(config.get("rerank_max_retries") or 0))
    raw_payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=float(config.get("rerank_timeout_seconds") or 10.0)) as response:
                raw_payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.warning("RAG rerank request failed attempt=%s error=%s", attempt + 1, exc)
            last_error = exc
            raw_payload = None
    if raw_payload is None:
        config["_rerank_runtime_available"] = False
        if last_error is not None:
            _record_runtime_capability_failure("rerank", provider=provider, error=last_error)
        return chunks

    results = raw_payload.get("results") if isinstance(raw_payload, dict) else None
    if not isinstance(results, list) or not results:
        return chunks

    ranked_items: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(rerank_candidates):
            continue
        score = float(item.get("relevance_score") or item.get("score") or 0.0)
        ranked_items.append((index, score))
    if not ranked_items:
        return chunks

    ordered: list[RetrievedChunk] = []
    seen_indexes: set[int] = set()
    for rank, (index, score) in enumerate(sorted(ranked_items, key=lambda item: item[1], reverse=True), start=1):
        chunk = rerank_candidates[index]
        chunk.rerank_score = score
        chunk.candidate_trace["rerank_rank"] = rank
        chunk.candidate_trace["external_rerank_score"] = score
        ordered.append(chunk)
        seen_indexes.add(index)
    for index, chunk in enumerate(rerank_candidates):
        if index in seen_indexes:
            continue
        ordered.append(chunk)
    return ordered + tail_chunks


def _estimate_embedding_tokens(message: str) -> int:
    raw = str(message or "")
    if not raw.strip():
        return 0
    try:
        return max(0, int(get_embedding_provider().count_tokens(raw)))
    except Exception:
        return max(1, len(raw.split()), (len(raw) + 3) // 4)


def _run_rag_query_legacy(
    message: str,
    top_k: int | None = None,
    *,
    product: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> RagQueryResult | None:
    config = _get_rag_config(top_k=top_k)
    resolved_table = _resolve_active_vector_table(config)
    if resolved_table:
        config["table"] = resolved_table
    if not config["dsn"] or not config["api_key"]:
        return None

    provider = get_embedding_provider()
    original_query = str(message or "").strip()
    vector_chunks: list[RetrievedChunk] = []
    bm25_chunks: list[RetrievedChunk] = []
    keyword_fallback_chunks: list[RetrievedChunk] = []
    chunks: list[RetrievedChunk] = []
    embedding_request_meta: list[dict[str, Any]] = []
    embedding_dimensions = getattr(provider, "vector_dim", None)
    query_type = _infer_query_type(message)
    query_understanding_enabled = _feature_flag_enabled("RAG_QUERY_UNDERSTANDING_ENABLED", True)
    query_rewrite_enabled = _feature_flag_enabled("RAG_QUERY_REWRITE_ENABLED", True)
    query_decomposition_enabled = _feature_flag_enabled("RAG_QUERY_DECOMPOSITION_ENABLED", True)
    query_expansion_enabled = _feature_flag_enabled("RAG_QUERY_EXPANSION_ENABLED", True)
    query_prf_enabled = _feature_flag_enabled("RAG_QUERY_PRF_ENABLED", True)
    query_understanding: QueryUnderstandingResult | None = None
    effective_hard_filters: dict[str, str] = {}
    effective_soft_signals: dict[str, list[str]] = {}
    effective_rule_expansions: list[str] = []
    effective_llm_expansions: list[str] = []
    effective_prf_expansions: list[str] = []
    effective_rewrites: list[str] = []
    effective_decomposition_subqueries: list[str] = []
    query_variants: list[tuple[str, str]] = [("original", str(message or "").strip())]
    first_pass_candidate_count = 0
    second_pass_candidate_count = 0
    total_started_at = time.perf_counter()
    vector_latency_ms = 0.0
    bm25_latency_ms = 0.0
    keyword_fallback_latency_ms = 0.0
    generation_latency_ms = 0.0
    rerank_latency_ms = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    model_name: str | None = None
    structured_retry_used = False
    generation_mode = "structured_answer"
    extractive_fallback_used = False
    packed_evidence: PackedEvidence | None = None
    retrieved_chunks: list[RetrievedChunk] = []
    reranked_chunks: list[RetrievedChunk] = []
    rerank_info: dict[str, Any] = {
        "hints": {"language": None, "method_name": None, "intent_terms": []},
        "applied_filter": False,
        "filter_type": None,
        "filtered_candidate_count": 0,
        "post_rerank_count": 0,
        "candidate_reasons": {},
    }
    query_understanding_executor: ThreadPoolExecutor | None = None
    query_understanding_future: Future[QueryUnderstandingResult] | None = None
    if query_understanding_enabled:
        _raise_if_cancelled(
            "query_understanding",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        query_understanding_executor = ThreadPoolExecutor(max_workers=1)
        query_understanding_future = query_understanding_executor.submit(understand_rag_query, message)
    effective_plan = RetrievalPlan(semantic_query=original_query)

    def _retrieval_config_for(plan: RetrievalPlan) -> dict[str, Any]:
        variant_config = dict(config)
        variant_config["_retrieval_plan"] = plan
        return variant_config

    def _collect_variants(variants: list[tuple[str, str]], plan: RetrievalPlan, *, keyword_limit: int) -> None:
        nonlocal bm25_chunks
        nonlocal bm25_latency_ms
        nonlocal embedding_request_meta
        nonlocal keyword_fallback_chunks
        nonlocal keyword_fallback_latency_ms
        nonlocal vector_chunks
        nonlocal vector_latency_ms
        variant_config = _retrieval_config_for(plan)
        for query_kind, variant_query in variants:
            _raise_if_cancelled(
                "vector_embedding",
                should_cancel=should_cancel,
                record_stage=record_cancel_stage,
            )
            try:
                vector_started_at = time.perf_counter()
                variant_vector_chunks = _retrieve_chunks(
                    variant_query,
                    variant_config,
                    limit=int(config["vector_candidate_k"]),
                )
                vector_latency_ms = round(vector_latency_ms + ((time.perf_counter() - vector_started_at) * 1000), 2)
                vector_chunks = _merge_variant_chunks(
                    vector_chunks,
                    variant_vector_chunks,
                    source_label="vector",
                    query_variant=variant_query,
                    query_kind=query_kind,
                )
            except Exception as exc:
                logger.warning("RAG retrieval failed for %s query: %s", query_kind, exc)
            finally:
                embedding_request_meta.extend(_drain_embedding_request_meta(provider))

            _raise_if_cancelled(
                "round_1_retrieval",
                should_cancel=should_cancel,
                record_stage=record_cancel_stage,
            )
            try:
                bm25_started_at = time.perf_counter()
                variant_bm25_chunks = _retrieve_bm25_chunks(
                    variant_query,
                    variant_config,
                    limit=int(config["bm25_candidate_k"]),
                )
                bm25_latency_ms = round(bm25_latency_ms + ((time.perf_counter() - bm25_started_at) * 1000), 2)
                bm25_chunks = _merge_variant_chunks(
                    bm25_chunks,
                    variant_bm25_chunks,
                    source_label="bm25",
                    query_variant=variant_query,
                    query_kind=query_kind,
                )
            except Exception as exc:
                logger.warning("RAG BM25 retrieval failed for %s query: %s", query_kind, exc)
                try:
                    keyword_started_at = time.perf_counter()
                    variant_keyword_chunks = _retrieve_keyword_chunks(
                        variant_query,
                        variant_config,
                        limit=keyword_limit,
                    )
                    keyword_fallback_latency_ms = round(
                        keyword_fallback_latency_ms + ((time.perf_counter() - keyword_started_at) * 1000),
                        2,
                    )
                    keyword_fallback_chunks = _merge_variant_chunks(
                        keyword_fallback_chunks,
                        variant_keyword_chunks,
                        source_label="keyword_fallback",
                        query_variant=variant_query,
                        query_kind=query_kind,
                    )
                except Exception as keyword_exc:
                    logger.warning("RAG keyword retrieval failed for %s query: %s", query_kind, keyword_exc)

    def _merge_candidate_sets() -> list[RetrievedChunk]:
        if vector_chunks and not bm25_chunks and keyword_fallback_chunks:
            return _merge_chunks(
                vector_chunks,
                keyword_fallback_chunks,
                limit=int(config["fusion_candidate_k"]),
            )
        if vector_chunks or bm25_chunks:
            merged_chunks = _rrf_merge(
                vector_chunks,
                bm25_chunks,
                limit=int(config["fusion_candidate_k"]),
            )
            if not merged_chunks:
                merged_chunks = _merge_chunks(
                    vector_chunks,
                    bm25_chunks,
                    limit=int(config["fusion_candidate_k"]),
                )
            return merged_chunks
        if keyword_fallback_chunks:
            return _merge_chunks(
                vector_chunks,
                keyword_fallback_chunks,
                limit=int(config["fusion_candidate_k"]),
            )
        return []

    _raise_if_cancelled(
        "query_understanding",
        should_cancel=should_cancel,
        record_stage=record_cancel_stage,
    )
    _collect_variants([("original", original_query)], effective_plan, keyword_limit=int(config["bm25_candidate_k"]))

    if query_understanding_future is not None:
        try:
            query_understanding = query_understanding_future.result()
        except Exception as exc:
            logger.warning("RAG query understanding failed: %s", exc)
        finally:
            if query_understanding_executor is not None:
                query_understanding_executor.shutdown(wait=True)
    if query_understanding is not None:
        effective_hard_filters = dict(query_understanding.retrieval_plan.hard_filters)
        effective_soft_signals = dict(query_understanding.retrieval_plan.soft_signals)
        effective_rule_expansions = list(query_understanding.retrieval_plan.rule_expansions) if query_expansion_enabled else []
        effective_llm_expansions = (
            list(query_understanding.retrieval_plan.llm_expansions or query_understanding.rewritten_queries)
            if query_rewrite_enabled
            else []
        )
        effective_rewrites = list(effective_llm_expansions)
        effective_decomposition_subqueries = (
            list(query_understanding.decomposition_subqueries) if query_decomposition_enabled else []
        )
        effective_plan = RetrievalPlan(
            semantic_query=query_understanding.semantic_query or original_query,
            hard_filters=dict(effective_hard_filters),
            soft_signals=dict(effective_soft_signals),
            rewritten_queries=list(effective_rewrites),
            decomposition_subqueries=list(effective_decomposition_subqueries),
            fallback_mode=query_understanding.fallback_mode,
            rule_expansions=list(effective_rule_expansions),
            llm_expansions=list(effective_llm_expansions),
            prf_expansions=[],
            hard_filter_sources=dict(query_understanding.retrieval_plan.hard_filter_sources),
            soft_signal_sources=dict(query_understanding.retrieval_plan.soft_signal_sources),
            cache_hit=bool(query_understanding.cache_hit),
            prf_used=False,
        )
        query_variants = _build_query_variants(
            message,
            replace(
                query_understanding,
                rewritten_queries=list(effective_rewrites),
                decomposition_subqueries=list(effective_decomposition_subqueries),
                retrieval_plan=effective_plan,
            ),
            rewrite_enabled=query_rewrite_enabled,
            decomposition_enabled=query_decomposition_enabled,
        )
        supplemental_variants = [(kind, query) for kind, query in query_variants if kind != "original"]
        if supplemental_variants:
            _collect_variants(supplemental_variants, effective_plan, keyword_limit=int(config["bm25_candidate_k"]))

    if not vector_chunks and not bm25_chunks:
        _raise_if_cancelled(
            "round_2_recovery",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        keyword_only_config = _retrieval_config_for(effective_plan)
        for query_kind, variant_query in query_variants:
            try:
                keyword_started_at = time.perf_counter()
                variant_keyword_chunks = _retrieve_keyword_chunks(
                    variant_query,
                    keyword_only_config,
                    limit=int(config["bm25_candidate_k"]),
                )
                keyword_fallback_latency_ms = round(
                    keyword_fallback_latency_ms + ((time.perf_counter() - keyword_started_at) * 1000),
                    2,
                )
                keyword_fallback_chunks = _merge_variant_chunks(
                    keyword_fallback_chunks,
                    variant_keyword_chunks,
                    source_label="keyword_fallback",
                    query_variant=variant_query,
                    query_kind=query_kind,
                )
            except Exception as exc:
                logger.warning("RAG keyword retrieval failed for %s query: %s", query_kind, exc)

    def _retrieval_strategy_for(*, keyword_fallback_used: bool) -> str:
        if keyword_fallback_used and vector_chunks:
            return "vector_keyword_fallback"
        if keyword_fallback_used:
            return "keyword_fallback"
        return "hybrid_rrf_bm25"

    chunks = _merge_candidate_sets()
    first_pass_candidate_count = len(chunks)

    weak_first_pass = (not chunks) or (not _has_grounded_keyword_overlap(message, chunks)) or (
        len(chunks) < min(2, int(config["top_k"]))
    )
    if query_prf_enabled and query_understanding is not None and weak_first_pass:
        _raise_if_cancelled(
            "round_2_recovery",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        prf_expansion_terms = build_prf_expansions(
            message,
            chunks,
            canonical_terms=query_understanding.canonical_terms,
            existing_expansions=[*effective_rule_expansions, *effective_llm_expansions],
        )
        if prf_expansion_terms:
            effective_prf_expansions = list(prf_expansion_terms)
            effective_plan = replace(
                effective_plan,
                prf_expansions=list(effective_prf_expansions),
                prf_used=True,
            )
            _collect_variants(
                [("prf", query) for query in effective_prf_expansions],
                effective_plan,
                keyword_limit=int(config["bm25_candidate_k"]),
            )
            chunks = _merge_candidate_sets()
    second_pass_candidate_count = len(chunks)

    if not chunks:
        keyword_only_config = _retrieval_config_for(effective_plan)
        for query_kind, variant_query in query_variants:
            try:
                keyword_started_at = time.perf_counter()
                variant_keyword_chunks = _retrieve_keyword_chunks(
                    variant_query,
                    keyword_only_config,
                    limit=int(config["top_k"]),
                )
                keyword_fallback_latency_ms = round(
                    keyword_fallback_latency_ms + ((time.perf_counter() - keyword_started_at) * 1000),
                    2,
                )
                keyword_fallback_chunks = _merge_variant_chunks(
                    keyword_fallback_chunks,
                    variant_keyword_chunks,
                    source_label="keyword_fallback",
                    query_variant=variant_query,
                    query_kind=query_kind,
                )
                chunks = list(keyword_fallback_chunks)
            except Exception as exc:
                logger.warning("RAG keyword retrieval failed for %s query: %s", query_kind, exc)
        if not chunks:
            query_meta = _query_understanding_meta(query_understanding, rerank_info)
            answer = RagAnswer(
                answer=INSUFFICIENT_EVIDENCE_REPLY,
                confidence=0.55,
                sources=[],
                citations=[],
            )
            trace = RagQueryTrace(
                query_type=query_type,
                retrieval_strategy=_retrieval_strategy_for(keyword_fallback_used=bool(keyword_fallback_chunks)),
                vector_candidates_count=len(vector_chunks),
                bm25_candidates_count=len(bm25_chunks),
                reranked_candidates_count=0,
                retrieved_chunk_ids=[],
                selected_chunk_ids=[],
                vector_retrieval_latency_ms=vector_latency_ms,
                bm25_retrieval_latency_ms=bm25_latency_ms,
                retrieval_latency_ms=round(vector_latency_ms + bm25_latency_ms + keyword_fallback_latency_ms, 2),
                rerank_latency_ms=0.0,
                generation_latency_ms=0.0,
                total_latency_ms=round((time.perf_counter() - total_started_at) * 1000, 2),
                prompt_tokens=0,
                completion_tokens=0,
                embedding_tokens=_estimate_embedding_tokens(message),
                embedding_provider=config["embedding_provider"],
                embedding_model=config["embedding_model"],
                embedding_dimensions=embedding_dimensions,
                embedding_request_meta=list(embedding_request_meta),
                model_name=None,
                answer_length=0,
                citation_count=0,
                cited_chunk_ids=[],
                needs_human=True,
                handoff_reason="insufficient_evidence",
                confidence_score=0.55,
                primary_source_type=None,
                primary_chunk_strategy=None,
                reranker_provider=config.get("rerank_provider"),
                reranker_model=config.get("rerank_model"),
                generation_mode="insufficient_evidence",
                selected_doc_count=0,
                top1_similarity_score=None,
                avg_selected_similarity_score=None,
                citation_coverage_ratio=None,
                retrieval_candidates=[],
                selected_contexts=[],
                metadata_hints=rerank_info.get("hints") if isinstance(rerank_info.get("hints"), dict) else {},
                metadata_filter_applied=bool(rerank_info.get("applied_filter")),
                metadata_filter_type=(str(rerank_info.get("filter_type")).strip() or None) if rerank_info.get("filter_type") is not None else None,
                intent_latency_ms=query_understanding.intent_latency_ms if query_understanding is not None else 0.0,
                rewrite_latency_ms=query_understanding.rewrite_latency_ms if query_understanding is not None else 0.0,
                query_understanding_enabled=query_understanding is not None,
                query_understanding_version=query_understanding.query_understanding_version if query_understanding is not None else None,
                query_profile=query_meta["query_profile"] or None,
                glossary_version=query_understanding.glossary_version if query_understanding is not None else None,
                self_query_version=query_understanding.self_query_version if query_understanding is not None else None,
                fallback_mode=query_meta["fallback_mode"] or None,
                glossary_hit_terms=list(query_meta["glossary_hit_terms"]),
                applied_hard_filters=dict(query_meta["applied_hard_filters"]),
                applied_soft_signals=dict(query_meta["applied_soft_signals"]),
                dictionary_hits=list(query_meta["dictionary_hits"]),
                rule_expansions=list(query_meta["rule_expansions"]),
                llm_expansions=list(query_meta["llm_expansions"]),
                prf_expansions=list(query_meta["prf_expansions"]),
                hard_filter_sources=dict(query_meta["hard_filter_sources"]),
                cache_hit=bool(query_meta["cache_hit"]),
                prf_used=bool(query_meta["prf_used"]),
                query_expansion_enabled=query_expansion_enabled,
                query_expansion_model=resolve_model_profile(QUERY_EXPANSION_SCENARIO).model if query_expansion_enabled else None,
                first_pass_candidate_count=first_pass_candidate_count,
                second_pass_candidate_count=second_pass_candidate_count,
                rewritten_queries=list(effective_rewrites),
                decomposition_subqueries=list(effective_decomposition_subqueries),
                context_budget_enabled=bool(config.get("context_budget_enabled")),
                context_window=int(config.get("context_window") or 0),
                reserved_output_tokens=int(config.get("reserved_output_tokens") or 0),
                buffer_tokens=int(config.get("buffer_tokens") or 0),
                raw_context_token_estimate=0,
                packed_context_token_estimate=0,
                compression_triggered=False,
                compression_trigger_reason=None,
                compression_mode="raw",
                compression_model=None,
                extractive_segment_count=0,
                packed_evidence_count=0,
                packed_context_text=None,
                packed_chunk_ids=[],
                query_expansion_usage_ledger=list(query_understanding.llm_usage_ledger) if query_understanding is not None else [],
                context_compression_usage_ledger=[],
                execution_mode="legacy",
                agent_fallback_used=False,
                agent_fallback_reason=None,
            )
            return RagQueryResult(answer=answer, trace=trace)

    retrieved_chunks = list(chunks)
    if chunks:
        _raise_if_cancelled(
            "rerank",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        rerank_started_at = time.perf_counter()
        reranked_chunks, rerank_info = _metadata_rerank(
            query=message,
            chunks=chunks,
            top_k=int(config["fusion_candidate_k"]),
            retrieval_plan=effective_plan,
            query_understanding=query_understanding,
        )
        rerank_latency_ms = round((time.perf_counter() - rerank_started_at) * 1000, 2)
        chunks = reranked_chunks or chunks
        chunks = _reorder_chunks_for_rerank(
            chunks,
            limit=int(config["rerank_top_n"]),
            query=message,
        ) or chunks
        rerank_started_at = time.perf_counter()
        externally_reranked = _rerank_chunks(
            message,
            chunks,
            config,
            limit=int(config["rerank_top_n"]),
        )
        rerank_latency_ms = round(rerank_latency_ms + ((time.perf_counter() - rerank_started_at) * 1000), 2)
        chunks = externally_reranked or chunks

    final_chunks = _select_diverse_chunks(chunks, limit=int(config["top_k"]), query=message) or chunks[: int(config["top_k"])] or chunks
    if chunks and bool(config.get("context_budget_enabled")):
        compression_defaults = resolve_model_profile(RAG_CONTEXT_COMPRESSION_SCENARIO)
        compression_profile = ModelProfile(
            scenario=RAG_CONTEXT_COMPRESSION_SCENARIO,
            provider="openai",
            model=str(config.get("context_compression_model") or "").strip() or compression_defaults.model,
            api_mode="openai_responses",
            api_key=str(config.get("api_key") or "").strip() or compression_defaults.api_key,
            reasoning_effort=str(config.get("context_compression_reasoning_effort") or "").strip()
            or compression_defaults.reasoning_effort,
            temperature=0.0,
            timeout_seconds=compression_defaults.timeout_seconds,
            max_retries=compression_defaults.max_retries,
            fallback_models=tuple(compression_defaults.fallback_models),
        )
        packing_limit = min(len(chunks), max(int(config.get("top_k") or 1), int(config.get("rerank_top_n") or len(chunks))))
        packing_candidates = list(chunks[:packing_limit]) or list(chunks)
        packed_evidence = build_packed_evidence(
            question=message,
            chunks=packing_candidates,
            system_prompt_text=_build_answer_system_prompt(product),
            user_prompt_text=_build_answer_prompt_for_mode(message, "", repair_mode=False),
            tool_schema_text="",
            context_window=int(config.get("context_window") or model_context_window(str(config.get("chat_model") or ""))),
            reserved_output_tokens=int(config.get("reserved_output_tokens") or 0),
            buffer_tokens=int(config.get("buffer_tokens") or 0),
            compression_enabled=bool(config.get("context_compression_enabled")),
            compression_profile=compression_profile,
            top_k=int(config.get("top_k") or 1),
        )
        packed_chunk_map = _chunk_map_by_id(packing_candidates)
        packed_chunks = [packed_chunk_map[chunk_id] for chunk_id in packed_evidence.chunk_ids if chunk_id in packed_chunk_map]
        if packed_chunks:
            final_chunks = packed_chunks
    allowed_chunk_ids = {chunk.chunk_id for chunk in final_chunks}
    grounded_overlap = _has_grounded_keyword_overlap(message, final_chunks)
    generation_config = dict(config)
    generation_config["reasoning_effort"] = _effective_answer_reasoning_effort(
        base_effort=str(config.get("reasoning_effort") or ""),
        query_class=_classify_agentic_query(message, query_understanding),
        query_type=query_type,
    )
    payload: dict[str, Any] | None = None
    try:
        _raise_if_cancelled(
            "answer_generation",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        generation_started_at = time.perf_counter()
        payload, prompt_tokens, completion_tokens, model_name = _invoke_llm_payload_with_trace(
            message,
            final_chunks,
            generation_config,
            strict_retry=False,
            packed_evidence=packed_evidence,
        )
        retry_required = (
            payload is None
            or not _is_valid_response(payload, allowed_chunk_ids)
            or (payload.get("insufficient_evidence") is True and grounded_overlap)
        )
        if retry_required:
            structured_retry_used = True
            retry_payload, retry_prompt_tokens, retry_completion_tokens, retry_model_name = _invoke_llm_payload_with_trace(
                message,
                final_chunks,
                generation_config,
                strict_retry=True,
                packed_evidence=packed_evidence,
            )
            prompt_tokens += retry_prompt_tokens
            completion_tokens += retry_completion_tokens
            model_name = retry_model_name or model_name
            payload = retry_payload
        generation_latency_ms = round((time.perf_counter() - generation_started_at) * 1000, 2)
    except Exception as exc:
        logger.warning("RAG answer generation failed: %s", exc)

    def _trace_for(
        answer: RagAnswer,
        *,
        needs_human: bool,
        handoff_reason: str | None,
        generation_mode: str,
        extractive_fallback_used: bool,
    ) -> RagQueryTrace:
        query_meta = _query_understanding_meta(query_understanding, rerank_info)
        cited_chunk_ids = [str(item.get("chunk_id")) for item in answer.citations if isinstance(item, dict) and item.get("chunk_id")]
        selected_chunk_ids = [chunk.chunk_id for chunk in final_chunks if chunk.chunk_id]
        unique_selected_chunk_ids = {chunk_id for chunk_id in selected_chunk_ids if chunk_id}
        top1_similarity_score = round(max(0.0, min(1.0, float(final_chunks[0].similarity))), 4) if final_chunks else None
        avg_selected_similarity_score = (
            round(sum(max(0.0, min(1.0, float(chunk.similarity))) for chunk in final_chunks) / len(final_chunks), 4)
            if final_chunks
            else None
        )
        citation_coverage_ratio = (
            round(len(set(cited_chunk_ids)) / len(unique_selected_chunk_ids), 4)
            if unique_selected_chunk_ids
            else None
        )
        return RagQueryTrace(
            query_type=query_type,
            retrieval_strategy=_retrieval_strategy_for(keyword_fallback_used=bool(keyword_fallback_chunks)),
            vector_candidates_count=len(vector_chunks),
            bm25_candidates_count=len(bm25_chunks),
            reranked_candidates_count=int(rerank_info.get("post_rerank_count") or 0),
            retrieved_chunk_ids=[chunk.chunk_id for chunk in retrieved_chunks if chunk.chunk_id],
            selected_chunk_ids=selected_chunk_ids,
            vector_retrieval_latency_ms=vector_latency_ms,
            bm25_retrieval_latency_ms=bm25_latency_ms,
            retrieval_latency_ms=round(vector_latency_ms + bm25_latency_ms + keyword_fallback_latency_ms, 2),
            rerank_latency_ms=rerank_latency_ms,
            generation_latency_ms=generation_latency_ms,
            total_latency_ms=round((time.perf_counter() - total_started_at) * 1000, 2),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            embedding_tokens=_estimate_embedding_tokens(message),
            embedding_provider=config["embedding_provider"],
            embedding_model=config["embedding_model"],
            embedding_dimensions=embedding_dimensions,
            embedding_request_meta=list(embedding_request_meta),
            model_name=model_name,
            answer_length=len(answer.answer.strip()) if answer.answer else 0,
            citation_count=len(cited_chunk_ids),
            cited_chunk_ids=cited_chunk_ids,
            needs_human=needs_human,
            handoff_reason=handoff_reason,
            confidence_score=answer.confidence,
            primary_source_type=_dominant_value(final_chunks, "source_type"),
            primary_chunk_strategy=_dominant_value(final_chunks, "chunk_strategy"),
            reranker_provider=str(config.get("rerank_provider") or "").strip() or None,
            reranker_model=str(config.get("rerank_model") or "").strip() or None,
            generation_mode=generation_mode,
            structured_retry_used=structured_retry_used,
            extractive_fallback_used=extractive_fallback_used,
            selected_doc_count=len(_unique_doc_ids(final_chunks)),
            top1_similarity_score=top1_similarity_score,
            avg_selected_similarity_score=avg_selected_similarity_score,
            citation_coverage_ratio=citation_coverage_ratio,
            retrieval_candidates=_candidate_rows(
                retrieved_chunks,
                reranked_chunks or chunks,
                selected_chunk_ids=unique_selected_chunk_ids,
            ),
            selected_contexts=(
                list(packed_evidence.selected_contexts)
                if packed_evidence is not None and packed_evidence.selected_contexts
                else _selected_contexts(final_chunks)
            ),
            metadata_hints=rerank_info.get("hints") if isinstance(rerank_info.get("hints"), dict) else {},
            metadata_filter_applied=bool(rerank_info.get("applied_filter")),
            metadata_filter_type=(str(rerank_info.get("filter_type")).strip() or None) if rerank_info.get("filter_type") is not None else None,
            intent_latency_ms=query_understanding.intent_latency_ms if query_understanding is not None else 0.0,
            rewrite_latency_ms=query_understanding.rewrite_latency_ms if query_understanding is not None else 0.0,
            query_understanding_enabled=query_understanding is not None,
            query_understanding_version=query_understanding.query_understanding_version if query_understanding is not None else None,
            query_profile=query_meta["query_profile"] or None,
            glossary_version=query_understanding.glossary_version if query_understanding is not None else None,
            self_query_version=query_understanding.self_query_version if query_understanding is not None else None,
            fallback_mode=query_meta["fallback_mode"] or None,
            glossary_hit_terms=list(query_meta["glossary_hit_terms"]),
            applied_hard_filters=dict(query_meta["applied_hard_filters"]),
            applied_soft_signals=dict(query_meta["applied_soft_signals"]),
            dictionary_hits=list(query_meta["dictionary_hits"]),
            rule_expansions=list(query_meta["rule_expansions"]),
            llm_expansions=list(query_meta["llm_expansions"]),
            prf_expansions=list(query_meta["prf_expansions"]),
            hard_filter_sources=dict(query_meta["hard_filter_sources"]),
            cache_hit=bool(query_meta["cache_hit"]),
            prf_used=bool(query_meta["prf_used"]),
            query_expansion_enabled=query_expansion_enabled,
            query_expansion_model=resolve_model_profile(QUERY_EXPANSION_SCENARIO).model if query_expansion_enabled else None,
            first_pass_candidate_count=first_pass_candidate_count,
            second_pass_candidate_count=second_pass_candidate_count,
            rewritten_queries=list(effective_rewrites),
            decomposition_subqueries=list(effective_decomposition_subqueries),
            context_budget_enabled=bool(config.get("context_budget_enabled")),
            context_window=int(config.get("context_window") or 0),
            reserved_output_tokens=int(config.get("reserved_output_tokens") or 0),
            buffer_tokens=int(config.get("buffer_tokens") or 0),
            raw_context_token_estimate=(
                int(packed_evidence.raw_context_token_estimate)
                if packed_evidence is not None
                else estimate_text_tokens(_format_context(final_chunks))
            ),
            packed_context_token_estimate=(
                int(packed_evidence.packed_context_token_estimate)
                if packed_evidence is not None
                else estimate_text_tokens(_format_context(final_chunks))
            ),
            compression_triggered=bool(packed_evidence.compression_triggered) if packed_evidence is not None else False,
            compression_trigger_reason=packed_evidence.compression_trigger_reason if packed_evidence is not None else None,
            compression_mode=packed_evidence.compression_mode if packed_evidence is not None else "raw",
            compression_model=packed_evidence.compression_model if packed_evidence is not None else None,
            extractive_segment_count=int(packed_evidence.extractive_segment_count) if packed_evidence is not None else 0,
            packed_evidence_count=int(packed_evidence.packed_evidence_count) if packed_evidence is not None else len(final_chunks),
            packed_context_text=packed_evidence.prompt_context if packed_evidence is not None else _format_context(final_chunks),
            packed_chunk_ids=list(packed_evidence.chunk_ids) if packed_evidence is not None else list(selected_chunk_ids),
            query_expansion_usage_ledger=list(query_understanding.llm_usage_ledger) if query_understanding is not None else [],
            context_compression_usage_ledger=list(packed_evidence.compression_usage_ledger) if packed_evidence is not None else [],
            execution_mode="legacy",
            agent_fallback_used=False,
            agent_fallback_reason=None,
        )

    if payload is not None and _is_valid_response(payload, allowed_chunk_ids):
        if payload["insufficient_evidence"] is True:
            if grounded_overlap:
                logger.info(
                    "RAG insufficient evidence persisted after grounded overlap was found. "
                    "Using extractive fallback."
                )
                generation_mode = "extractive_fallback"
                extractive_fallback_used = True
                answer = _build_extractive_rag_answer(final_chunks)
                return RagQueryResult(
                    answer=answer,
                    trace=_trace_for(
                        answer,
                        needs_human=False,
                        handoff_reason=None,
                        generation_mode=generation_mode,
                        extractive_fallback_used=extractive_fallback_used,
                    ),
                )
            answer = RagAnswer(
                answer=INSUFFICIENT_EVIDENCE_REPLY,
                confidence=0.55,
                sources=[],
                citations=[],
            )
            generation_mode = "insufficient_evidence"
            return RagQueryResult(
                answer=answer,
                trace=_trace_for(
                    answer,
                    needs_human=True,
                    handoff_reason="insufficient_evidence",
                    generation_mode=generation_mode,
                    extractive_fallback_used=False,
                ),
            )
        citations = [str(chunk_id) for chunk_id in payload["citations"]]
        citation_records = _citation_records_from_ids(citations, final_chunks)
        sources = [
            record.get("source_url") or f"rag:{record['chunk_id']}"
            for record in citation_records
        ]
        answer = RagAnswer(
            answer=_build_answer_text(str(payload["answer"]), payload.get("key_steps", [])),
            confidence=_confidence_from_chunks(final_chunks),
            sources=sources,
            citations=citation_records,
        )
        generation_mode = "structured_answer"
        return RagQueryResult(
            answer=answer,
            trace=_trace_for(
                answer,
                needs_human=False,
                handoff_reason=None,
                generation_mode=generation_mode,
                extractive_fallback_used=False,
            ),
        )

    logger.warning("RAG structured answer invalid, using extractive fallback.")
    generation_mode = "extractive_fallback"
    extractive_fallback_used = True
    answer = _build_extractive_rag_answer(final_chunks)
    return RagQueryResult(
        answer=answer,
        trace=_trace_for(
            answer,
            needs_human=False,
            handoff_reason=None,
            generation_mode=generation_mode,
            extractive_fallback_used=extractive_fallback_used,
        ),
    )


def _merge_retrieved_chunk_map(
    chunk_map: dict[str, RetrievedChunk],
    incoming: list[RetrievedChunk],
) -> None:
    for item in incoming:
        chunk = _copy_chunk(item)
        dedupe_key = _chunk_dedupe_key(chunk)
        existing = chunk_map.get(dedupe_key)
        if existing is None:
            chunk_map[dedupe_key] = chunk
            continue
        merged_sources = list(dict.fromkeys([*existing.retrieval_sources, *chunk.retrieval_sources]))
        if float(chunk.similarity or 0.0) > float(existing.similarity or 0.0):
            chunk.retrieval_sources = merged_sources
            chunk_map[dedupe_key] = chunk
            continue
        existing.retrieval_sources = merged_sources


def _iteration_trace_payload(iteration: AgenticIterationTrace) -> dict[str, Any]:
    return {
        "round_index": int(iteration.round_index),
        "tool_names": list(iteration.tool_names),
        "query_variants": list(iteration.query_variants),
        "selected_chunk_ids": list(iteration.selected_chunk_ids),
        "decision": str(iteration.decision),
        "recovery_action": iteration.recovery_action,
    }


def _run_rag_query_agentic(
    message: str,
    top_k: int | None = None,
    *,
    ticket_context: list[dict[str, str]] | None = None,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    product: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> RagQueryResult | None:
    _ = ticket_id
    _ = customer_id
    request_started_at = time.perf_counter()
    config = _get_rag_config(top_k=top_k)
    if not config["dsn"] or not config["api_key"]:
        return None

    query_type = _infer_query_type(message)
    preliminary_query_class = _classify_agentic_query(message, None)
    simple_lexical_query = preliminary_query_class == "lexical_exact" and _is_simple_lexical_query(message)
    vector_setup_skipped = simple_lexical_query
    light_path_used = simple_lexical_query
    preflight_probe_latency_ms = 0.0

    config["_vector_runtime_available"] = (
        False
        if vector_setup_skipped
        else bool(config.get("vector_enabled", True))
        and _runtime_capability_available(
            "vector",
            provider=str(config.get("embedding_provider") or ""),
        )
    )
    config["_rerank_runtime_available"] = bool(config.get("rerank_enabled", True)) and _runtime_capability_available(
        "rerank",
        provider=str(config.get("rerank_provider") or ""),
    )

    provider: Any | None = None
    embedding_dimensions = None
    if not vector_setup_skipped:
        resolved_table = _resolve_active_vector_table(config)
        if resolved_table:
            config["table"] = resolved_table
    if bool(config.get("vector_enabled")) and not vector_setup_skipped:
        try:
            provider = get_embedding_provider()
            embedding_dimensions = getattr(provider, "vector_dim", None)
        except Exception as exc:
            logger.warning("RAG vector retrieval disabled for this query: %s", exc)
            config["vector_enabled"] = False
            config["_vector_runtime_available"] = False
    preflight_probe_latency_ms = round((time.perf_counter() - request_started_at) * 1000, 2)
    warm_vector_enabled = bool(config.get("vector_enabled")) and not simple_lexical_query
    query_understanding_enabled = _feature_flag_enabled("RAG_QUERY_UNDERSTANDING_ENABLED", True) and not simple_lexical_query
    query_rewrite_enabled = _feature_flag_enabled("RAG_QUERY_REWRITE_ENABLED", True)
    query_decomposition_enabled = _feature_flag_enabled("RAG_QUERY_DECOMPOSITION_ENABLED", True)
    query_expansion_enabled = _feature_flag_enabled("RAG_QUERY_EXPANSION_ENABLED", True)
    query_understanding: QueryUnderstandingResult | None = None
    effective_hard_filters: dict[str, str] = {}
    effective_soft_signals: dict[str, list[str]] = {}
    effective_rule_expansions: list[str] = []
    effective_llm_expansions: list[str] = []
    effective_prf_expansions: list[str] = []
    effective_rewrites: list[str] = []
    effective_decomposition_subqueries: list[str] = []
    effective_query_understanding: QueryUnderstandingResult | None = None
    first_pass_candidate_count = 0
    second_pass_candidate_count = 0
    total_started_at = request_started_at
    vector_latency_ms = 0.0
    bm25_latency_ms = 0.0
    keyword_fallback_latency_ms = 0.0
    rerank_latency_ms = 0.0
    generation_latency_ms = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    model_name: str | None = None
    structured_retry_used = False
    generation_mode = "structured_answer"
    extractive_fallback_used = False
    answer_profile_used: str | None = None
    answer_profile_fallback_used = False
    retrieved_chunk_map: dict[str, RetrievedChunk] = {}
    reranked_chunks: list[RetrievedChunk] = []
    final_chunks: list[RetrievedChunk] = []
    final_rerank_info: dict[str, Any] = {
        "hints": {"language": None, "method_name": None, "intent_terms": []},
        "applied_filter": False,
        "filter_type": None,
        "filtered_candidate_count": 0,
        "post_rerank_count": 0,
        "candidate_reasons": {},
    }
    final_judge: AgenticJudgeDecision | None = None
    recovery_action: str | None = None
    agent_iterations: list[dict[str, Any]] = []
    total_vector_candidates = 0
    total_bm25_candidates = 0
    query_understanding_executor: ThreadPoolExecutor | None = None
    query_understanding_future: Future[QueryUnderstandingResult] | None = None
    warm_original_vector_future: Future[tuple[list[RetrievedChunk], float]] | None = None
    warm_original_bm25_future: Future[tuple[list[RetrievedChunk], float]] | None = None
    warm_original_vector_chunks: list[RetrievedChunk] = []
    warm_original_bm25_chunks: list[RetrievedChunk] = []
    warm_original_vector_latency_ms = 0.0
    warm_original_bm25_latency_ms = 0.0

    def _timed_retrieve(
        retrieval_fn: Any,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[list[RetrievedChunk], float]:
        started_at = time.perf_counter()
        chunks = retrieval_fn(*args, **kwargs)
        return list(chunks or []), round((time.perf_counter() - started_at) * 1000, 2)

    if query_understanding_enabled:
        _raise_if_cancelled(
            "query_understanding",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        query_understanding_executor = ThreadPoolExecutor(max_workers=3 if warm_vector_enabled else 2)
        warm_retrieval_config = dict(config)
        warm_retrieval_config["_retrieval_plan"] = RetrievalPlan(semantic_query=str(message or "").strip())
        query_understanding_future = query_understanding_executor.submit(understand_rag_query, message)
        if warm_vector_enabled:
            warm_original_vector_future = query_understanding_executor.submit(
                _timed_retrieve,
                _retrieve_chunks,
                message,
                warm_retrieval_config,
                limit=int(config.get("vector_candidate_k") or config.get("top_k") or 5),
                index_role="primary",
            )
        warm_original_bm25_future = query_understanding_executor.submit(
            _timed_retrieve,
            _retrieve_bm25_chunks,
            message,
            warm_retrieval_config,
            limit=int(config.get("bm25_candidate_k") or config.get("top_k") or 5),
            index_role="primary",
        )
    if query_understanding_future is not None:
        try:
            query_understanding = query_understanding_future.result()
        except Exception as exc:
            logger.warning("RAG query understanding failed: %s", exc)
        if warm_original_vector_future is not None:
            try:
                warm_original_vector_chunks, warm_original_vector_latency_ms = warm_original_vector_future.result()
            except Exception as exc:
                config["_vector_runtime_available"] = False
                logger.warning("Agentic warm vector retrieval failed: %s", exc)
        if warm_original_bm25_future is not None:
            try:
                warm_original_bm25_chunks, warm_original_bm25_latency_ms = warm_original_bm25_future.result()
            except Exception as exc:
                logger.warning("Agentic warm BM25 retrieval failed: %s", exc)
        if query_understanding_executor is not None:
            query_understanding_executor.shutdown(wait=True)
    if query_understanding is not None:
        effective_hard_filters = dict(query_understanding.retrieval_plan.hard_filters)
        effective_soft_signals = dict(query_understanding.retrieval_plan.soft_signals)
        effective_rule_expansions = list(query_understanding.retrieval_plan.rule_expansions) if query_expansion_enabled else []
        effective_llm_expansions = (
            list(query_understanding.retrieval_plan.llm_expansions or query_understanding.rewritten_queries)
            if query_rewrite_enabled
            else []
        )
        effective_rewrites = list(effective_llm_expansions)
        effective_decomposition_subqueries = (
            list(query_understanding.decomposition_subqueries) if query_decomposition_enabled else []
        )
        effective_plan = RetrievalPlan(
            semantic_query=query_understanding.semantic_query or str(message or "").strip(),
            hard_filters=dict(effective_hard_filters),
            soft_signals=dict(effective_soft_signals),
            rewritten_queries=list(effective_rewrites),
            decomposition_subqueries=list(effective_decomposition_subqueries),
            fallback_mode=query_understanding.fallback_mode,
            rule_expansions=list(effective_rule_expansions),
            llm_expansions=list(effective_llm_expansions),
            prf_expansions=list(effective_prf_expansions),
            hard_filter_sources=dict(query_understanding.retrieval_plan.hard_filter_sources),
            soft_signal_sources=dict(query_understanding.retrieval_plan.soft_signal_sources),
            cache_hit=bool(query_understanding.cache_hit),
            prf_used=bool(query_understanding.retrieval_plan.prf_used),
        )
        effective_query_understanding = replace(
            query_understanding,
            rewritten_queries=list(effective_rewrites),
            decomposition_subqueries=list(effective_decomposition_subqueries),
            retrieval_plan=effective_plan,
        )
    else:
        effective_plan = RetrievalPlan(semantic_query=str(message or "").strip())

    plan = _build_agentic_retrieval_plan(
        message=message,
        top_k=int(config["top_k"]),
        query_understanding=effective_query_understanding or query_understanding,
        ticket_context=ticket_context,
        product=product,
        should_cancel=should_cancel,
        record_cancel_stage=record_cancel_stage,
    )
    if plan.light_path:
        config = _apply_light_path_latency_budget(config)
        light_path_used = True
    warm_seed_tool_results = {
        tool_name: chunks
        for tool_name, chunks in {
            "p_vec": warm_original_vector_chunks,
            "p_bm25": warm_original_bm25_chunks,
        }.items()
        if chunks
    }

    for round_index in [1, 2]:
        if round_index == 2:
            _raise_if_cancelled(
                "round_2_recovery",
                should_cancel=should_cancel,
                record_stage=record_cancel_stage,
            )
        round_result = _execute_agentic_round(
            message=message,
            config=config,
            plan=plan,
            round_index=round_index,
            retrieval_plan=effective_plan,
            query_understanding=effective_query_understanding or query_understanding,
            ticket_context=ticket_context,
            recovery_action=recovery_action,
            seed_tool_results=warm_seed_tool_results if round_index == 1 else None,
            should_cancel=should_cancel,
            record_cancel_stage=record_cancel_stage,
        )
        if round_index == 1 and "p_vec" in round_result.used_seed_tools:
            vector_latency_ms += warm_original_vector_latency_ms
        if round_index == 1 and "p_bm25" in round_result.used_seed_tools:
            bm25_latency_ms += warm_original_bm25_latency_ms
        vector_latency_ms += round_result.vector_latency_ms
        bm25_latency_ms += round_result.bm25_latency_ms
        keyword_fallback_latency_ms += round_result.keyword_latency_ms
        rerank_latency_ms += round_result.rerank_latency_ms
        total_vector_candidates += round_result.vector_candidate_count
        total_bm25_candidates += round_result.bm25_candidate_count
        _merge_retrieved_chunk_map(retrieved_chunk_map, round_result.retrieved_chunks)
        reranked_chunks = list(round_result.reranked_chunks)
        final_chunks = list(round_result.final_chunks)
        final_rerank_info = dict(round_result.rerank_info)
        final_judge = round_result.judge
        agent_iterations.append(_iteration_trace_payload(round_result.iteration_trace))
        if round_index == 1:
            first_pass_candidate_count = len(round_result.retrieved_chunks)
        second_pass_candidate_count = len(round_result.retrieved_chunks)
        if round_result.judge.decision == "recover_once" and round_index == 1:
            recovery_action = round_result.judge.recovery_action
            continue
        break

    retrieved_chunks = list(retrieved_chunk_map.values())
    packed_evidence: PackedEvidence | None = None

    def _trace_for(
        answer: RagAnswer,
        *,
        needs_human: bool,
        handoff_reason: str | None,
        generation_mode: str,
        extractive_fallback_used: bool,
    ) -> RagQueryTrace:
        query_meta = _query_understanding_meta(effective_query_understanding or query_understanding, final_rerank_info)
        cited_chunk_ids = [str(item.get("chunk_id")) for item in answer.citations if isinstance(item, dict) and item.get("chunk_id")]
        selected_chunk_ids = [chunk.chunk_id for chunk in final_chunks if chunk.chunk_id]
        unique_selected_chunk_ids = {chunk_id for chunk_id in selected_chunk_ids if chunk_id}
        top1_similarity_score = round(max(0.0, min(1.0, float(final_chunks[0].similarity))), 4) if final_chunks else None
        avg_selected_similarity_score = (
            round(sum(max(0.0, min(1.0, float(chunk.similarity))) for chunk in final_chunks) / len(final_chunks), 4)
            if final_chunks
            else None
        )
        citation_coverage_ratio = (
            round(len(set(cited_chunk_ids)) / len(unique_selected_chunk_ids), 4)
            if unique_selected_chunk_ids
            else None
        )
        primary_shadow_mix = {
            "primary": sum(1 for chunk in final_chunks if str(chunk.index_role or "").strip().lower() == "primary"),
            "shadow": sum(1 for chunk in final_chunks if str(chunk.index_role or "").strip().lower() == "shadow"),
        }
        return RagQueryTrace(
            query_type=query_type,
            retrieval_strategy="agentic_multi_tool_v1",
            vector_candidates_count=total_vector_candidates,
            bm25_candidates_count=total_bm25_candidates,
            reranked_candidates_count=int(final_rerank_info.get("post_rerank_count") or 0),
            retrieved_chunk_ids=[chunk.chunk_id for chunk in retrieved_chunks if chunk.chunk_id],
            selected_chunk_ids=selected_chunk_ids,
            vector_retrieval_latency_ms=round(vector_latency_ms, 2),
            bm25_retrieval_latency_ms=round(bm25_latency_ms + keyword_fallback_latency_ms, 2),
            retrieval_latency_ms=round(vector_latency_ms + bm25_latency_ms + keyword_fallback_latency_ms, 2),
            rerank_latency_ms=round(rerank_latency_ms, 2),
            generation_latency_ms=round(generation_latency_ms, 2),
            total_latency_ms=round((time.perf_counter() - total_started_at) * 1000, 2),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            embedding_tokens=_estimate_embedding_tokens(message),
            embedding_provider=config["embedding_provider"],
            embedding_model=config["embedding_model"],
            embedding_dimensions=embedding_dimensions,
            embedding_request_meta=list(_drain_embedding_request_meta(provider)),
            model_name=model_name,
            answer_length=len(answer.answer.strip()) if answer.answer else 0,
            citation_count=len(cited_chunk_ids),
            cited_chunk_ids=cited_chunk_ids,
            needs_human=needs_human,
            handoff_reason=handoff_reason,
            confidence_score=answer.confidence,
            primary_source_type=_dominant_value(final_chunks, "source_type"),
            primary_chunk_strategy=_dominant_value(final_chunks, "chunk_strategy"),
            reranker_provider=str(config.get("rerank_provider") or "").strip() or None,
            reranker_model=str(config.get("rerank_model") or "").strip() or None,
            generation_mode=generation_mode,
            structured_retry_used=structured_retry_used,
            extractive_fallback_used=extractive_fallback_used,
            selected_doc_count=len(_unique_doc_ids(final_chunks)),
            top1_similarity_score=top1_similarity_score,
            avg_selected_similarity_score=avg_selected_similarity_score,
            citation_coverage_ratio=citation_coverage_ratio,
            retrieval_candidates=_candidate_rows(
                retrieved_chunks,
                reranked_chunks,
                selected_chunk_ids=unique_selected_chunk_ids,
            ),
            selected_contexts=(
                list(packed_evidence.selected_contexts)
                if packed_evidence is not None and packed_evidence.selected_contexts
                else _selected_contexts(final_chunks)
            ),
            metadata_hints=final_rerank_info.get("hints") if isinstance(final_rerank_info.get("hints"), dict) else {},
            metadata_filter_applied=bool(final_rerank_info.get("applied_filter")),
            metadata_filter_type=(
                (str(final_rerank_info.get("filter_type")).strip() or None)
                if final_rerank_info.get("filter_type") is not None
                else None
            ),
            intent_latency_ms=query_understanding.intent_latency_ms if query_understanding is not None else 0.0,
            rewrite_latency_ms=query_understanding.rewrite_latency_ms if query_understanding is not None else 0.0,
            query_understanding_enabled=(effective_query_understanding or query_understanding) is not None,
            query_understanding_version=query_understanding.query_understanding_version if query_understanding is not None else None,
            query_profile=query_meta["query_profile"] or None,
            glossary_version=query_understanding.glossary_version if query_understanding is not None else None,
            self_query_version=query_understanding.self_query_version if query_understanding is not None else None,
            fallback_mode=query_meta["fallback_mode"] or None,
            glossary_hit_terms=list(query_meta["glossary_hit_terms"]),
            applied_hard_filters=dict(query_meta["applied_hard_filters"]),
            applied_soft_signals=dict(query_meta["applied_soft_signals"]),
            dictionary_hits=list(query_meta["dictionary_hits"]),
            rule_expansions=list(query_meta["rule_expansions"]),
            llm_expansions=list(query_meta["llm_expansions"]),
            prf_expansions=list(query_meta["prf_expansions"]),
            hard_filter_sources=dict(query_meta["hard_filter_sources"]),
            cache_hit=bool(query_meta["cache_hit"]),
            prf_used=bool(query_meta["prf_used"]),
            query_expansion_enabled=query_expansion_enabled,
            query_expansion_model=resolve_model_profile(QUERY_EXPANSION_SCENARIO).model if query_expansion_enabled else None,
            first_pass_candidate_count=first_pass_candidate_count,
            second_pass_candidate_count=second_pass_candidate_count,
            rewritten_queries=list(effective_rewrites),
            decomposition_subqueries=list(effective_decomposition_subqueries),
            agent_enabled=True,
            agent_plan_version=AGENT_PLAN_VERSION,
            query_class=plan.query_class,
            agent_iterations=list(agent_iterations),
            agent_recovery_action=recovery_action,
            ticket_context_used=bool(ticket_context),
            primary_shadow_mix=primary_shadow_mix,
            context_budget_enabled=bool(config.get("context_budget_enabled")),
            context_window=int(config.get("context_window") or 0),
            reserved_output_tokens=int(config.get("reserved_output_tokens") or 0),
            buffer_tokens=int(config.get("buffer_tokens") or 0),
            raw_context_token_estimate=(
                int(packed_evidence.raw_context_token_estimate)
                if packed_evidence is not None
                else estimate_text_tokens(_format_context(final_chunks))
            ),
            packed_context_token_estimate=(
                int(packed_evidence.packed_context_token_estimate)
                if packed_evidence is not None
                else estimate_text_tokens(_format_context(final_chunks))
            ),
            compression_triggered=bool(packed_evidence.compression_triggered) if packed_evidence is not None else False,
            compression_trigger_reason=packed_evidence.compression_trigger_reason if packed_evidence is not None else None,
            compression_mode=packed_evidence.compression_mode if packed_evidence is not None else "raw",
            compression_model=packed_evidence.compression_model if packed_evidence is not None else None,
            extractive_segment_count=int(packed_evidence.extractive_segment_count) if packed_evidence is not None else 0,
            packed_evidence_count=int(packed_evidence.packed_evidence_count) if packed_evidence is not None else len(final_chunks),
            packed_context_text=packed_evidence.prompt_context if packed_evidence is not None else _format_context(final_chunks),
            packed_chunk_ids=list(packed_evidence.chunk_ids) if packed_evidence is not None else list(selected_chunk_ids),
            query_expansion_usage_ledger=list(query_understanding.llm_usage_ledger) if query_understanding is not None else [],
            context_compression_usage_ledger=list(packed_evidence.compression_usage_ledger) if packed_evidence is not None else [],
            execution_mode="agentic",
            agent_fallback_used=False,
            agent_fallback_reason=None,
            preflight_probe_latency_ms=preflight_probe_latency_ms,
            vector_setup_skipped=vector_setup_skipped,
            light_path_used=light_path_used,
            answer_profile_used=answer_profile_used,
            answer_profile_fallback_used=answer_profile_fallback_used,
        )

    if not final_chunks or final_judge is None or final_judge.decision == "escalate":
        answer = RagAnswer(
            answer=INSUFFICIENT_EVIDENCE_REPLY,
            confidence=0.55,
            sources=[],
            citations=[],
        )
        handoff_reason = final_judge.reason if final_judge is not None else "insufficient_evidence"
        return RagQueryResult(
            answer=answer,
            trace=_trace_for(
                answer,
                needs_human=True,
                handoff_reason=handoff_reason,
                generation_mode="insufficient_evidence",
                extractive_fallback_used=False,
            ),
        )

    allowed_chunk_ids = {chunk.chunk_id for chunk in final_chunks if chunk.chunk_id}
    grounded_overlap = _has_grounded_keyword_overlap(message, final_chunks)
    generation_config = dict(config)
    generation_config["reasoning_effort"] = _effective_answer_reasoning_effort(
        base_effort=str(config.get("reasoning_effort") or ""),
        query_class=plan.query_class,
        query_type=query_type,
    )
    if plan.light_path:
        generation_chunk_limit = int(
            generation_config.get("light_path_generation_chunk_limit") or _LIGHT_PATH_CONTEXT_CHUNK_LIMIT
        )
        final_chunks = list(final_chunks[:generation_chunk_limit])
        allowed_chunk_ids = {chunk.chunk_id for chunk in final_chunks if chunk.chunk_id}
        grounded_overlap = _has_grounded_keyword_overlap(message, final_chunks)
    if final_chunks and bool(config.get("context_budget_enabled")):
        _raise_if_cancelled(
            "answer_generation",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        compression_defaults = resolve_model_profile(RAG_CONTEXT_COMPRESSION_SCENARIO)
        compression_profile = ModelProfile(
            scenario=RAG_CONTEXT_COMPRESSION_SCENARIO,
            provider="openai",
            model=str(config.get("context_compression_model") or "").strip() or compression_defaults.model,
            api_mode="openai_responses",
            api_key=str(config.get("api_key") or "").strip() or compression_defaults.api_key,
            reasoning_effort=str(config.get("context_compression_reasoning_effort") or "").strip()
            or compression_defaults.reasoning_effort,
            temperature=0.0,
            timeout_seconds=compression_defaults.timeout_seconds,
            max_retries=compression_defaults.max_retries,
            fallback_models=tuple(compression_defaults.fallback_models),
        )
        packed_evidence = build_packed_evidence(
            question=message,
            chunks=list(final_chunks),
            system_prompt_text=_build_answer_system_prompt(product),
            user_prompt_text=_build_answer_prompt_for_mode(message, "", repair_mode=False),
            tool_schema_text="",
            context_window=int(config.get("context_window") or model_context_window(str(config.get("chat_model") or ""))),
            reserved_output_tokens=int(config.get("reserved_output_tokens") or 0),
            buffer_tokens=int(config.get("buffer_tokens") or 0),
            compression_enabled=bool(config.get("context_compression_enabled")),
            compression_profile=compression_profile,
            history_text=" ".join(
                " ".join(str(item.get("content") or "").split()).strip()
                for item in list(ticket_context or [])
                if str(item.get("content") or "").strip()
            ),
            top_k=int(config.get("top_k") or 1),
        )
        packed_chunk_map = _chunk_map_by_id(final_chunks)
        packed_chunks = [packed_chunk_map[chunk_id] for chunk_id in packed_evidence.chunk_ids if chunk_id in packed_chunk_map]
        if packed_chunks:
            final_chunks = packed_chunks
            allowed_chunk_ids = {chunk.chunk_id for chunk in final_chunks if chunk.chunk_id}
            grounded_overlap = _has_grounded_keyword_overlap(message, final_chunks)
    payload: dict[str, Any] | None = None
    generation_started_at = time.perf_counter()
    fast_answer_profile = (
        _build_answer_profile(generation_config, use_light_path_fast_model=True)
        if plan.light_path and final_judge.decision == "answer_now"
        else None
    )
    primary_answer_profile = _build_answer_profile(generation_config)
    _raise_if_cancelled(
        "answer_generation",
        should_cancel=should_cancel,
        record_stage=record_cancel_stage,
    )
    initial_profile = fast_answer_profile or primary_answer_profile
    payload, prompt_tokens, completion_tokens, model_name = _invoke_llm_payload_with_trace(
        message,
        final_chunks,
        generation_config,
        strict_retry=False,
        packed_evidence=packed_evidence,
        product=product,
        profile_override=initial_profile,
    )
    answer_profile_used = model_name or initial_profile.model
    retry_required = (
        payload is None
        or not _is_valid_response(payload, allowed_chunk_ids)
        or (payload.get("insufficient_evidence") is True and grounded_overlap)
    )
    if retry_required and fast_answer_profile is not None:
        answer_profile_fallback_used = True
        _raise_if_cancelled(
            "answer_generation",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        retry_payload, retry_prompt_tokens, retry_completion_tokens, retry_model_name = _invoke_llm_payload_with_trace(
            message,
            final_chunks,
            generation_config,
            strict_retry=False,
            packed_evidence=packed_evidence,
            product=product,
            profile_override=primary_answer_profile,
        )
        prompt_tokens += retry_prompt_tokens
        completion_tokens += retry_completion_tokens
        model_name = retry_model_name or model_name
        answer_profile_used = model_name or primary_answer_profile.model
        payload = retry_payload
        retry_required = (
            payload is None
            or not _is_valid_response(payload, allowed_chunk_ids)
            or (payload.get("insufficient_evidence") is True and grounded_overlap)
        )
    if retry_required:
        structured_retry_used = True
        _raise_if_cancelled(
            "answer_generation",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        retry_payload, retry_prompt_tokens, retry_completion_tokens, retry_model_name = _invoke_llm_payload_with_trace(
            message,
            final_chunks,
            generation_config,
            strict_retry=True,
            packed_evidence=packed_evidence,
            product=product,
            profile_override=primary_answer_profile,
        )
        prompt_tokens += retry_prompt_tokens
        completion_tokens += retry_completion_tokens
        model_name = retry_model_name or model_name
        answer_profile_used = model_name or primary_answer_profile.model
        payload = retry_payload
    generation_latency_ms = (time.perf_counter() - generation_started_at) * 1000

    if payload is not None and _is_valid_response(payload, allowed_chunk_ids):
        if payload["insufficient_evidence"] is True:
            if grounded_overlap:
                generation_mode = "extractive_fallback"
                extractive_fallback_used = True
                answer = _build_extractive_rag_answer(final_chunks)
                return RagQueryResult(
                    answer=answer,
                    trace=_trace_for(
                        answer,
                        needs_human=False,
                        handoff_reason=None,
                        generation_mode=generation_mode,
                        extractive_fallback_used=extractive_fallback_used,
                    ),
                )
            answer = RagAnswer(
                answer=INSUFFICIENT_EVIDENCE_REPLY,
                confidence=0.55,
                sources=[],
                citations=[],
            )
            return RagQueryResult(
                answer=answer,
                trace=_trace_for(
                    answer,
                    needs_human=True,
                    handoff_reason="insufficient_evidence",
                    generation_mode="insufficient_evidence",
                    extractive_fallback_used=False,
                ),
            )
        citations = [str(chunk_id) for chunk_id in payload["citations"]]
        citation_records = _citation_records_from_ids(citations, final_chunks)
        sources = [
            record.get("source_url") or f"rag:{record['chunk_id']}"
            for record in citation_records
        ]
        answer = RagAnswer(
            answer=_build_answer_text(str(payload["answer"]), payload.get("key_steps", [])),
            confidence=_confidence_from_chunks(final_chunks),
            sources=sources,
            citations=citation_records,
        )
        return RagQueryResult(
            answer=answer,
            trace=_trace_for(
                answer,
                needs_human=False,
                handoff_reason=None,
                generation_mode="structured_answer",
                extractive_fallback_used=False,
            ),
        )

    generation_mode = "extractive_fallback"
    extractive_fallback_used = True
    answer = _build_extractive_rag_answer(final_chunks)
    return RagQueryResult(
        answer=answer,
        trace=_trace_for(
            answer,
            needs_human=False,
            handoff_reason=None,
            generation_mode=generation_mode,
            extractive_fallback_used=extractive_fallback_used,
        ),
    )


def run_rag_query(
    message: str,
    top_k: int | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    product: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> RagQueryResult | None:
    if not _feature_flag_enabled("RAG_AGENT_ENABLED", True):
        result = _run_rag_query_legacy(
            message,
            top_k=top_k,
            product=product,
            should_cancel=should_cancel,
            record_cancel_stage=record_cancel_stage,
        )
        if result is None:
            return None
        result.trace.execution_mode = "legacy"
        result.trace.agent_fallback_used = False
        result.trace.agent_fallback_reason = None
        return result
    try:
        result = _run_rag_query_agentic(
            message,
            top_k=top_k,
            ticket_context=ticket_context,
            ticket_id=ticket_id,
            customer_id=customer_id,
            product=product,
            should_cancel=should_cancel,
            record_cancel_stage=record_cancel_stage,
        )
        if result is not None:
            result.trace.execution_mode = "agentic"
            result.trace.agent_fallback_used = False
            result.trace.agent_fallback_reason = None
        return result
    except RagExecutionCancelled:
        raise
    except Exception as exc:
        logger.warning("Agentic RAG failed, falling back to legacy flow: %s", exc)
        result = _run_rag_query_legacy(
            message,
            top_k=top_k,
            product=product,
            should_cancel=should_cancel,
            record_cancel_stage=record_cancel_stage,
        )
        if result is None:
            return None
        result.trace.execution_mode = "legacy"
        result.trace.agent_fallback_used = True
        result.trace.agent_fallback_reason = exc.__class__.__name__ or "agentic_fallback"
        return result


def answer_with_rag(message: str, top_k: int | None = None) -> RagAnswer | None:
    """
    Attempt to answer with PostgreSQL pgvector retrieval + LangChain answer generation.
    Returns None when RAG is not configured or retrieval fails, so caller can fallback.
    """
    result = run_rag_query(message, top_k=top_k)
    return result.answer if result is not None else None

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import Any, Callable, Iterable

from backend.services.api_semantics import (
    build_anchor_variant,
    extract_anchor_hits,
    extract_endpoint_operation_hints,
    extract_numbered_subqueries,
    is_api_semantics_mismatch_message,
)
from backend.services.client_query_intent import (
    has_explicit_troubleshooting_signal,
    is_answer_first_how_to_message,
    resolve_follow_up_example_inheritance,
)
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
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.customer_reply_composer import (
    compose_customer_reply_email,
    detect_customer_reply_language,
)
from backend.services.rag_context_budget import (
    PackedEvidence,
    build_packed_evidence,
    estimate_text_tokens,
    model_context_window,
)
from backend.services.rag_deadline import RagDeadline
from backend.services.rag_request_body_evidence import (
    RequestBodyEvidenceResult,
    detect_request_body_evidence_query,
    is_high_value_troubleshooting_context,
    merge_request_body_evidence_chunks,
    run_request_body_evidence_skill,
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
    SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING,
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
_API_SEMANTICS_CONTEXT_CHUNK_LIMIT = 2
_API_SEMANTICS_FAST_ANSWER_MODEL = "gpt-5.4-nano"
_API_SEMANTICS_FAST_ANSWER_REASONING_EFFORT = "low"
_API_SEMANTICS_BM25_CANDIDATE_K = 36
_API_SEMANTICS_RERANK_TOP_N = 16
_SHORT_FAQ_RECOVERY_BM25_CANDIDATE_K = 8
_SHORT_FAQ_RECOVERY_FTS_CANDIDATE_K = 0
_SHORT_FAQ_RECOVERY_FUSION_CANDIDATE_K = 6
_SHORT_FAQ_RECOVERY_RERANK_TOP_N = 4
_SHORT_FAQ_CONTEXT_CHUNK_LIMIT = 2
_HOW_TO_FAQ_PREFIXES = (
    "how to ",
    "how do i ",
    "how can i ",
    "how do we ",
    "how can we ",
)
_HOW_TO_FAQ_USAGE_TERMS = {
    "create",
    "destroy",
    "join",
    "joinchannel",
    "leave",
    "login",
    "logout",
    "mute",
    "publish",
    "receive",
    "send",
    "start",
    "stop",
    "subscribe",
    "switch",
    "unmute",
}
_LIGHT_PATH_JOIN_RECOVERY_BM25_CANDIDATE_K = 24
_LIGHT_PATH_JOIN_RECOVERY_FTS_CANDIDATE_K = 24
_LIGHT_PATH_JOIN_RECOVERY_FUSION_CANDIDATE_K = 18
_GENERIC_JOIN_FOCUSED_VARIANT_KINDS = frozenset({"focused_rewrite", "focused_join_step"})
_JOIN_CHANNEL_PATTERN = re.compile(r"\bjoin(?:\s+(?:a|the))?\s+channel\b|\bjoinchannel\b", flags=re.IGNORECASE)
_AUDIO_VIDEO_CALLING_CORE_PRODUCTS = frozenset({"video-calling", "voice-calling"})
_AUDIO_VIDEO_CALLING_SECONDARY_PRODUCTS = frozenset({"interactive-live-streaming", "broadcast-streaming"})
_AUDIO_VIDEO_CALLING_PENALIZED_PRODUCTS = frozenset({"signaling", "iot", "cloud-recording"})
_TOKEN_USAGE_FOCUSED_VARIANT_KINDS = frozenset({"focused_token_usage", "focused_rewrite"})
_CONNECTION_STATE_FOCUSED_VARIANT_KINDS = frozenset({"focused_reference", "focused_rewrite"})
_API_SEMANTICS_MAX_FANOUT_CHILDREN = 3
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
    query_class: str | None = None
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
    query_policy: str | None = None
    glossary_version: str | None = None
    self_query_version: str | None = None
    fallback_mode: str | None = None
    glossary_hit_terms: list[str] = field(default_factory=list)
    applied_hard_filters: dict[str, str] = field(default_factory=dict)
    downpushed_hard_filters: dict[str, str] = field(default_factory=dict)
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
    first_pass_tools: list[str] = field(default_factory=list)
    plan_query_variants: list[dict[str, Any]] = field(default_factory=list)
    plan_decomposition_targets: list[str] = field(default_factory=list)
    evidence_goal: str | None = None
    recovery_bias: str | None = None
    judge_summary: dict[str, Any] = field(default_factory=dict)
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
    shadow_retrieval_enabled: bool = True
    shadow_tools_skipped: list[str] = field(default_factory=list)
    bm25_sql_latency_ms: float = 0.0
    fts_latency_ms: float = 0.0
    retrieval_round_wall_clock_ms: float = 0.0
    retrieval_tool_timings: list[dict[str, Any]] = field(default_factory=list)
    variant_candidate_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    variant_zero_yield_reasons: list[dict[str, Any]] = field(default_factory=list)
    fanout_used: bool = False
    fanout_child_count: int = 0
    fanout_children: list[dict[str, Any]] = field(default_factory=list)
    deadline_exhausted: bool = False
    anchor_hits: list[str] = field(default_factory=list)
    timeout_stage: str | None = None
    doc_family_mix: dict[str, int] = field(default_factory=dict)
    generic_join_primary_chunk_found: bool = False
    generic_join_support_pair_found: bool = False
    generic_join_support_chunks: list[str] = field(default_factory=list)
    generic_join_recovery_used: bool = False
    answer_path_decision: str | None = None
    effective_question: str | None = None
    follow_up_inheritance_used: bool = False
    follow_up_inheritance_source: str | None = None
    request_body_skill_triggered: bool = False
    request_body_keys: list[str] = field(default_factory=list)
    request_body_nested_paths: list[str] = field(default_factory=list)
    request_body_endpoint_hints: list[str] = field(default_factory=list)
    request_body_missing_evidence: list[str] = field(default_factory=list)
    request_body_evidence_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class RagQueryResult:
    answer: RagAnswer
    trace: RagQueryTrace


@dataclass(frozen=True)
class RagKnowledgeIndexReadiness:
    status: str
    configured_table: str | None
    resolved_table: str | None
    configured_primary_rows: int | None


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


def _resolve_effective_question(
    message: str,
    ticket_context: list[dict[str, str]] | None,
) -> tuple[str, Any | None]:
    normalized_message = " ".join(str(message or "").split()).strip()
    follow_up_inheritance = resolve_follow_up_example_inheritance(
        message=normalized_message,
        ticket_context=ticket_context,
    )
    if follow_up_inheritance is None:
        return normalized_message, None
    return str(follow_up_inheritance.effective_question or normalized_message).strip(), follow_up_inheritance


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
    product: str | None = None
    shadow_tools_skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgenticQueryFlags:
    preliminary_query_class: str
    api_semantics_query: bool
    short_how_to_faq_query: bool
    simple_lexical_query: bool
    vector_setup_skipped: bool
    light_path_used: bool
    skip_bm25_warmup: bool


@dataclass(frozen=True)
class AgenticFeatureFlags:
    query_understanding_enabled: bool
    query_rewrite_enabled: bool
    query_decomposition_enabled: bool
    query_expansion_enabled: bool
    warm_vector_enabled: bool


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
    shadow_tools_skipped: list[str] = field(default_factory=list)


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
    fts_latency_ms: float = 0.0
    keyword_latency_ms: float = 0.0
    retrieval_wall_clock_ms: float = 0.0
    retrieval_tool_timings: list[dict[str, Any]] = field(default_factory=list)
    rerank_latency_ms: float = 0.0
    used_seed_tools: list[str] = field(default_factory=list)
    shadow_tools_skipped: list[str] = field(default_factory=list)


def _feature_flag_enabled(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _shadow_retrieval_enabled(config: dict[str, Any] | None = None) -> bool:
    if isinstance(config, dict) and "shadow_retrieval_enabled" in config:
        return bool(config.get("shadow_retrieval_enabled"))
    return _feature_flag_enabled("RAG_SHADOW_RETRIEVAL_ENABLED", True)


def _is_shadow_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().lower().startswith("s_")


def _filter_shadow_tool_names(
    tool_names: Iterable[str],
    *,
    shadow_retrieval_enabled: bool,
) -> tuple[list[str], list[str]]:
    filtered: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for tool_name in tool_names:
        normalized = str(tool_name or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if not shadow_retrieval_enabled and _is_shadow_tool(normalized):
            skipped.append(normalized)
            continue
        filtered.append(normalized)
    return filtered, skipped


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


def _is_how_to_faq_query(
    message: str,
    understanding: QueryUnderstandingResult | None = None,
) -> bool:
    normalized = " ".join(str(message or "").split()).strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    answer_first_how_to = is_answer_first_how_to_message(normalized)
    if not any(lowered.startswith(prefix) for prefix in _HOW_TO_FAQ_PREFIXES) and not answer_first_how_to:
        return False
    if re.search(r"\b(error|code)\s+\d{3,5}\b", lowered):
        return False
    if has_explicit_troubleshooting_signal(lowered) or any(
        marker in lowered for marker in ["jitter", "root cause", "why ", "问题", "排查", "故障"]
    ):
        return False
    if _is_multiple_channels_query(normalized) or _is_stream_channel_query(normalized):
        return False
    query_terms = _extract_query_terms(normalized, max_terms=8)
    if not answer_first_how_to and (len(normalized.split()) > 8 or len(query_terms) > 5):
        return False
    if any(term in _HOW_TO_FAQ_USAGE_TERMS for term in query_terms):
        return True
    soft_signals = (
        dict(understanding.retrieval_plan.soft_signals)
        if understanding is not None and isinstance(understanding.retrieval_plan.soft_signals, dict)
        else {}
    )
    return bool(soft_signals.get("use_case"))


def _is_simple_lexical_query(message: str) -> bool:
    normalized = " ".join(str(message or "").split()).strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if re.search(r"\b(error|code)\s+\d{3,5}\b", lowered):
        return len(normalized.split()) <= 6 and len(_extract_query_terms(normalized, max_terms=8)) <= 4
    if re.search(r"\b\d{3,5}\b", lowered):
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
    def _variant_raw_score(chunk: RetrievedChunk) -> float:
        trace = chunk.candidate_trace if isinstance(chunk.candidate_trace, dict) else {}
        for key in ["raw_score", "bm25_score", "fts_rank", "vector_similarity", "keyword_fallback_hits"]:
            value = trace.get(key)
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return float(chunk.similarity or 0.0)

    def _sort_key(chunk: RetrievedChunk) -> tuple[float, float, int, str]:
        trace = chunk.candidate_trace if isinstance(chunk.candidate_trace, dict) else {}
        variants = trace.get("query_variants")
        variant_count = len(variants) if isinstance(variants, list) else 0
        return (
            float(chunk.similarity or 0.0),
            _variant_raw_score(chunk),
            variant_count,
            str(chunk.chunk_id or ""),
        )

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
        incoming_raw_score = _variant_raw_score(chunk)
        existing_raw_score = _variant_raw_score(existing_chunk)
        existing_chunk.similarity = max(existing_similarity, incoming_similarity)
        existing_chunk.retrieval_sources = list(dict.fromkeys([*existing_chunk.retrieval_sources, *chunk.retrieval_sources]))
        existing_variants = existing_chunk.candidate_trace.get("query_variants")
        if not isinstance(existing_variants, list):
            existing_variants = []
        for variant in variant_traces:
            if variant not in existing_variants:
                existing_variants.append(variant)
        existing_chunk.candidate_trace["query_variants"] = existing_variants
        if incoming_similarity > existing_similarity or (
            incoming_similarity == existing_similarity and incoming_raw_score > existing_raw_score
        ):
            existing_chunk.candidate_trace.update(chunk.candidate_trace)
    return sorted(merged.values(), key=_sort_key, reverse=True)


def _build_query_variants(
    message: str,
    understanding: QueryUnderstandingResult | None,
    *,
    rewrite_enabled: bool,
    decomposition_enabled: bool,
) -> list[tuple[str, str]]:
    variants: list[tuple[str, str]] = [("original", str(message or "").strip())]
    if understanding is None:
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


def _usage_configuration_query_variants(
    normalized_message: str,
    understanding: QueryUnderstandingResult | None,
) -> list[tuple[str, str]]:
    variants: list[tuple[str, str]] = [("original", normalized_message)]
    if understanding is None:
        return _dedupe_agentic_variants(variants)

    semantic_query = " ".join(str(understanding.semantic_query or "").split()).strip()
    if semantic_query:
        variants.append(("semantic", semantic_query))
    for query in understanding.retrieval_plan.rule_expansions:
        variants.append(("rule", str(query).strip()))
    for query in understanding.retrieval_plan.llm_expansions or understanding.rewritten_queries:
        variants.append(("rewrite", str(query).strip()))
    for query in understanding.decomposition_subqueries:
        variants.append(("decomposition", str(query).strip()))
    return _dedupe_agentic_variants(variants)


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
    if is_api_semantics_mismatch_message(message):
        return "api_semantics_mismatch"
    if _is_how_to_faq_query(message, understanding):
        return "usage_configuration"
    if understanding is not None:
        doc_subtype = str(understanding.retrieval_plan.hard_filters.get("doc_subtype") or "").strip().lower()
        if doc_subtype == "troubleshooting_case":
            return "troubleshooting_why"
    if any(term in lowered for term in ["why", "root cause", "black screen", "no audio", "jitter", "delay", "failed", "failure", "问题", "故障", "排查"]):
        return "troubleshooting_why"
    if any(term in lowered for term in ["configure", "configuration", "setup", "enable", "disable", "deploy", "parameter", "参数", "配置"]):
        return "usage_configuration"
    if _extract_query_terms(message, max_terms=6) or re.search(r"\b\d{3,5}\b", lowered):
        return "lexical_exact"
    return "unclear_query"


def _raw_tool_order_for_query_class(query_class: str) -> tuple[list[str], str, str]:
    if query_class == "api_semantics_mismatch":
        return (["p_bm25", "p_fts", "p_vec"], "api_semantics_grounding", "lexical")
    if query_class == "lexical_exact":
        return (["p_bm25", "p_fts", "p_vec"], "exact_match", "lexical")
    if query_class == "how_to_faq":
        return (["p_bm25", "p_fts", "p_vec"], "how_to_usage_support", "lexical")
    if query_class == "usage_configuration":
        return (["p_bm25", "p_fts"], "configuration_support", "lexical")
    if query_class == "unclear_query":
        return (["p_bm25", "p_fts"], "clarifying_evidence", "conservative")
    if query_class == "troubleshooting_why":
        return (["p_vec", "s_vec", "p_bm25", "s_bm25", "p_fts", "s_fts"], "causal_grounding", "semantic")
    if query_class == "comparison":
        return (["p_vec", "p_bm25", "s_vec", "p_fts"], "balanced_comparison", "compare")
    return (["p_bm25", "p_vec", "p_fts", "s_bm25", "s_vec"], "configuration_support", "lexical")


def _tool_order_for_query_class(
    query_class: str,
    *,
    shadow_retrieval_enabled: bool | None = None,
) -> tuple[list[str], str, str]:
    if shadow_retrieval_enabled is None:
        shadow_retrieval_enabled = _shadow_retrieval_enabled()
    raw_tool_names, evidence_goal, recovery_bias = _raw_tool_order_for_query_class(query_class)
    filtered_tool_names, _ = _filter_shadow_tool_names(
        raw_tool_names,
        shadow_retrieval_enabled=shadow_retrieval_enabled,
    )
    return filtered_tool_names, evidence_goal, recovery_bias


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


def _normalized_query_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _is_multiple_channels_query(message: str) -> bool:
    lower = _normalized_query_text(message)
    return any(
        marker in lower
        for marker in ["multiple channels", "multiple channel", "multi-channel", "multichannel", "joinchannelex"]
    )


def _is_stream_channel_query(message: str) -> bool:
    lower = _normalized_query_text(message)
    return any(marker in lower for marker in ["stream channel", "stream channels", "signaling", "rtm"])


def _is_generic_join_channel_query(message: str) -> bool:
    lower = _normalized_query_text(message)
    if not lower or not _JOIN_CHANNEL_PATTERN.search(lower):
        return False
    if _is_multiple_channels_query(lower) or _is_stream_channel_query(lower):
        return False
    return True


def _is_generic_token_usage_query(message: str) -> bool:
    lower = _normalized_query_text(message)
    if "token" not in lower:
        return False
    if any(
        marker in lower
        for marker in [
            " error",
            " errors",
            " failed",
            " failure",
            " issue",
            " issues",
            " problem",
            " problems",
            " handle ",
            " handle token",
            " troubleshooting",
            " why ",
        ]
    ):
        return False
    return any(
        lower.startswith(marker)
        for marker in [
            "how to use token",
            "use token",
            "using token",
            "token authentication",
        ]
    )


def _is_connection_state_reference_query(message: str) -> bool:
    lower = _normalized_query_text(message)
    return "connection state change" in lower or "onconnectionstatechanged" in lower


def _short_lexical_faq_pattern(message: str) -> str | None:
    if _is_generic_join_channel_query(message):
        return "join_channel"
    if _is_generic_token_usage_query(message):
        return "token_usage"
    if _is_connection_state_reference_query(message):
        return "connection_state"
    return None


def _is_short_lexical_faq_bucket(message: str, plan: AgenticRetrievalPlan) -> bool:
    if plan.query_class not in {"lexical_exact", "how_to_faq", "usage_configuration"}:
        return False
    query_terms = _extract_query_terms(message, max_terms=8)
    if len(query_terms) > 6:
        return False
    return _short_lexical_faq_pattern(message) is not None


def _is_short_how_to_faq_query(
    message: str,
    understanding: QueryUnderstandingResult | None = None,
) -> bool:
    if not _is_how_to_faq_query(message, understanding):
        return False
    normalized = " ".join(str(message or "").split()).strip()
    if not normalized or len(normalized) > 96 or "\n" in str(message or ""):
        return False
    query_terms = _extract_query_terms(normalized, max_terms=10)
    return bool(query_terms) and len(query_terms) <= 8


def _is_short_symptom_troubleshooting_query(message: str) -> bool:
    normalized = " ".join(str(message or "").split()).strip()
    if not normalized or len(normalized) > 120 or "\n" in str(message or ""):
        return False
    lowered = normalized.lower()
    if "http://" in lowered or "https://" in lowered:
        return False
    query_terms = _extract_query_terms(normalized, max_terms=12)
    if not query_terms or len(query_terms) > 9:
        return False
    return any(
        marker in lowered
        for marker in [
            "black screen",
            "blank screen",
            "no audio",
            "video freeze",
            "freeze",
            "frozen",
            "stutter",
            "jitter",
            "lag",
            "echo",
            "crash",
        ]
    )


def _allows_release_note_guidance_for_short_symptom_query(message: str) -> bool:
    normalized = _normalized_query_text(message)
    if not normalized or not _is_short_symptom_troubleshooting_query(normalized):
        return False
    if any(
        marker in normalized
        for marker in [
            "why ",
            "root cause",
            "reason",
            "investigate",
            "debug",
            "log",
            "trace",
        ]
    ):
        return False
    return any(
        marker in normalized
        for marker in [
            "what should i do",
            "what can i do",
            "how should i fix",
            "how can i fix",
            "how do i fix",
            "how to fix",
            "what should i check",
        ]
    )


def _is_black_screen_faq_chunk(chunk: RetrievedChunk) -> bool:
    surface = _chunk_surface_text(chunk)
    return "black screen" in surface and any(
        marker in surface
        for marker in [
            "how can i fix black screen issues?",
            "frequently asked questions",
            "faq",
        ]
    )


def _is_black_screen_release_note_chunk(chunk: RetrievedChunk) -> bool:
    return _is_release_note_chunk(chunk) and "black screen" in _chunk_surface_text(chunk)


def _select_black_screen_guidance_chunks(
    chunks: list[RetrievedChunk],
    *,
    product: str | None,
) -> tuple[RetrievedChunk | None, RetrievedChunk | None]:
    compatible_chunks = [chunk for chunk in chunks if _generic_join_product_compatible(chunk, product)]
    candidates = compatible_chunks or list(chunks)
    faq_chunk = next((chunk for chunk in candidates if _is_black_screen_faq_chunk(chunk)), None)
    release_note_chunk = next((chunk for chunk in candidates if _is_black_screen_release_note_chunk(chunk)), None)
    return release_note_chunk, faq_chunk


def _has_black_screen_guidance_support(
    chunks: list[RetrievedChunk],
    *,
    product: str | None,
) -> bool:
    release_note_chunk, faq_chunk = _select_black_screen_guidance_chunks(
        chunks,
        product=product,
    )
    return release_note_chunk is not None and faq_chunk is not None


def _short_lexical_faq_recovery_variants(message: str, exact_terms: list[str]) -> list[tuple[str, str]]:
    exact_query = " ".join(exact_terms).strip()
    pattern = _short_lexical_faq_pattern(message)
    if pattern == "join_channel":
        return _dedupe_agentic_variants(
            [
                ("focused_join_step", "join a channel joinChannel channelName uid token appid quickstart get started"),
                ("focused_rewrite", "join channel joinChannel token channel name uid basic authentication"),
            ]
        )
    if pattern == "token_usage":
        return _dedupe_agentic_variants(
            [
                ("exact_token", exact_query or "use token"),
                ("focused_token_usage", "use token token authentication token server basic authentication join channel"),
                ("focused_rewrite", "token authentication use token app server token join channel"),
            ]
        )
    if pattern == "connection_state":
        return _dedupe_agentic_variants(
            [
                ("exact_token", exact_query or "connection state change"),
                ("focused_reference", "connection state change onConnectionStateChanged connection state callback state changed"),
                ("focused_rewrite", "connection state change callback purpose api reference state transition"),
            ]
        )
    return []


def _generic_join_recovery_variants() -> list[tuple[str, str]]:
    return _dedupe_agentic_variants(
        [
            ("focused_join_step", "join a channel joinChannel channelName uid token appid quickstart get started"),
            ("focused_rewrite", "join channel joinChannel token channel name uid basic authentication"),
        ]
    )


def _original_query_text_for_plan(message: str, plan: AgenticRetrievalPlan) -> str:
    for query_kind, query_text in list(plan.query_variants or []):
        normalized_query = " ".join(str(query_text or "").split()).strip()
        if query_kind == "original" and normalized_query:
            return normalized_query
    return " ".join(str(message or "").split()).strip()


def _load_short_faq_original_tool_results(
    *,
    message: str,
    plan: AgenticRetrievalPlan,
    config: dict[str, Any],
    lexical_result_cache: dict[tuple[str, str, str, int], tuple[str, list[RetrievedChunk]]] | None,
) -> tuple[str, dict[str, list[RetrievedChunk]]]:
    original_query_text = _original_query_text_for_plan(message, plan)
    if not original_query_text:
        return "", {}

    original_tool_results: dict[str, list[RetrievedChunk]] = {}
    for tool_name in ("p_bm25", "p_fts", "p_keyword"):
        cache_key = _lexical_cache_key(
            tool_name=tool_name,
            query_text=original_query_text,
            index_role="primary",
            candidate_k=_tool_candidate_k(config, tool_name),
        )
        cached_result = _lookup_lexical_cache(
            lexical_result_cache,
            cache_key=cache_key,
        )
        if cached_result is None:
            continue
        cached_tool_name, cached_chunks = cached_result
        normalized_tool_name = str(cached_tool_name or tool_name).strip() or tool_name
        original_tool_results[normalized_tool_name] = [_copy_chunk(chunk) for chunk in cached_chunks]
    return original_query_text, original_tool_results


def _merge_tool_result_maps(
    *tool_result_maps: dict[str, list[RetrievedChunk]],
) -> dict[str, list[RetrievedChunk]]:
    merged: dict[str, list[RetrievedChunk]] = {}
    seen_by_tool: dict[str, set[str]] = {}
    for tool_result_map in tool_result_maps:
        for tool_name, chunks in (tool_result_map or {}).items():
            normalized_tool_name = str(tool_name or "").strip()
            if not normalized_tool_name:
                continue
            target = merged.setdefault(normalized_tool_name, [])
            seen = seen_by_tool.setdefault(normalized_tool_name, set())
            for chunk in list(chunks or []):
                dedupe_key = _chunk_dedupe_key(chunk)
                if dedupe_key and dedupe_key in seen:
                    continue
                target.append(_copy_chunk(chunk))
                if dedupe_key:
                    seen.add(dedupe_key)
    return merged


def _short_lexical_faq_recovery_requests(
    message: str,
    exact_terms: list[str],
    *,
    original_chunks: list[RetrievedChunk] | None = None,
    product: str | None = None,
) -> list[tuple[str, str, str]]:
    requests: list[tuple[str, str, str]] = []
    pattern = _short_lexical_faq_pattern(message)
    if pattern == "join_channel":
        has_join_step, has_token_auth = _generic_join_support_signals(
            list(original_chunks or []),
            product=product,
            query=message,
        )
        for query_kind, query_text in _short_lexical_faq_recovery_variants(message, exact_terms):
            if query_kind == "focused_join_step":
                if has_join_step:
                    continue
                requests.append(("p_bm25", query_kind, query_text))
                continue
            if query_kind == "focused_rewrite" and has_token_auth:
                continue
            requests.append(("p_bm25", query_kind, query_text))
        return requests

    for query_kind, query_text in _short_lexical_faq_recovery_variants(message, exact_terms):
        requests.append(("p_bm25", query_kind, query_text))
    return requests


def _short_lexical_faq_exact_terms(message: str) -> list[str]:
    pattern = _short_lexical_faq_pattern(message)
    if pattern == "join_channel":
        return _extract_query_terms("join channel", max_terms=6)
    if pattern == "token_usage":
        return _extract_query_terms("use token", max_terms=6)
    if pattern == "connection_state":
        return _extract_query_terms("connection state change", max_terms=6)
    return _extract_query_terms(message, max_terms=6)


def _chunk_source_family(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    return str(metadata.get("source_family") or "").strip().replace("\\", "/").strip("/").lower()


def _chunk_product(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    return (
        str(metadata.get("product") or "").strip().lower()
        or str(metadata.get("product_area") or "").strip().lower()
    )


def _chunk_platform(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    platform = str(metadata.get("platform") or "").strip().lower()
    if platform:
        return platform
    source_path = str(chunk.source_path or "").strip().lower()
    match = re.search(r"_([a-z0-9-]+)\.md$", source_path)
    return match.group(1) if match else ""


def _chunk_surface_text(chunk: RetrievedChunk) -> str:
    parts = [
        chunk.source_path,
        _chunk_source_family(chunk),
        chunk.h1,
        chunk.h2,
        chunk.h3,
        chunk.text,
    ]
    return " ".join(str(part or "").strip().lower() for part in parts if str(part or "").strip())


def _is_join_multiple_channels_chunk(chunk: RetrievedChunk) -> bool:
    surface = _chunk_surface_text(chunk)
    return any(marker in surface for marker in ["join-multiple-channels", "join multiple channels", "joinchannelex"])


def _is_stream_channel_chunk(chunk: RetrievedChunk) -> bool:
    surface = _chunk_surface_text(chunk)
    return any(marker in surface for marker in ["stream-channel", "stream channel", "signaling/stream-channel"])


def _is_release_note_chunk(chunk: RetrievedChunk) -> bool:
    surface = _chunk_surface_text(chunk)
    return any(
        marker in surface
        for marker in [
            "release notes",
            "release-notes",
            "issue fixed",
            "issues fixed",
            "fixed issue",
            "fixed issues",
            "known issues",
            "release note",
        ]
    )


def _is_join_channel_step_chunk(chunk: RetrievedChunk) -> bool:
    surface = _chunk_surface_text(chunk)
    return (
        any(
            marker in surface
            for marker in ["join a channel", "join channel", "joinchannel(", "joinchannel ", " call joinchannel"]
        )
        and not _is_token_auth_chunk(chunk)
        and not _is_join_multiple_channels_chunk(chunk)
        and not _is_stream_channel_chunk(chunk)
    )


def _generic_join_requires_role_agnostic_guidance(query: str) -> bool:
    normalized = _normalized_query_text(query)
    if not _is_generic_join_channel_query(normalized):
        return False
    return not any(
        marker in normalized
        for marker in [
            "live",
            "broadcast",
            "broadcaster",
            "host",
            "audience",
            "client role",
            "clientrole",
            "setclientrole",
            "role switch",
        ]
    )


def _is_role_specific_join_chunk(chunk: RetrievedChunk) -> bool:
    if not _is_join_channel_step_chunk(chunk):
        return False
    surface = _chunk_surface_text(chunk)
    return any(
        marker in surface
        for marker in [
            "clientroletype",
            "setclientrole",
            "broadcaster",
            "livebroadcasting",
            "channelprofile",
            "audiencelatencylevel",
        ]
    )


def _is_token_auth_chunk(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    use_case = str(metadata.get("use_case") or "").strip().lower()
    source_family = _chunk_source_family(chunk)
    source_path = str(chunk.source_path or "").strip().lower()
    surface = _chunk_surface_text(chunk)
    if use_case == "basic_authentication":
        return True
    if "authentication-workflow" in source_path or "token-authentication" in source_family:
        return True
    auth_markers = [
        "basic authentication",
        "token authentication",
        "use tokens",
        "use a token to join a channel",
        "request a token",
        "token server",
        "authentication server",
        "access token",
        "app certificate",
        "authentication workflow",
    ]
    return "token" in surface and any(marker in surface for marker in auth_markers)


def _is_preferred_generic_join_step_chunk(chunk: RetrievedChunk) -> bool:
    if not _is_join_channel_step_chunk(chunk):
        return False
    if _is_role_specific_join_chunk(chunk):
        return False
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    use_case = str(metadata.get("use_case") or "").strip().lower()
    source_path = str(chunk.source_path or "").strip().lower()
    heading = " ".join(
        str(part or "").strip().lower()
        for part in [chunk.h1, chunk.h2, chunk.h3]
        if str(part or "").strip()
    )
    if use_case == "basic_authentication":
        return False
    if "authentication-workflow" in source_path:
        return False
    if "basic authentication" in heading or "use tokens" in heading:
        return False
    return True


def _join_step_chunk_mentions_token(chunk: RetrievedChunk) -> bool:
    if not _is_join_channel_step_chunk(chunk):
        return False
    surface = _chunk_surface_text(chunk)
    return "token" in surface


def _chunk_covers_generic_join_auth_prerequisite(chunk: RetrievedChunk) -> bool:
    return _is_token_auth_chunk(chunk) or _join_step_chunk_mentions_token(chunk)


def _token_auth_chunk_has_join_flow(chunk: RetrievedChunk) -> bool:
    if not _is_token_auth_chunk(chunk):
        return False
    text = " ".join(str(part or "").strip().lower() for part in [chunk.text] if str(part or "").strip())
    if not text:
        return False
    has_join_invocation = any(
        marker in text
        for marker in [
            "joinchannel(",
            "joinchannel ",
            "call the sdk join method",
            "join method",
            "join the channel",
        ]
    )
    has_join_parameters = (
        any(marker in text for marker in ["channel name", "channel id", "channelid"])
        and any(marker in text for marker in ["user id", "uid"])
    )
    has_code_example = "```" in str(chunk.text or "")
    return has_join_invocation or (has_join_parameters and has_code_example)


def _chunk_covers_generic_join_step(chunk: RetrievedChunk) -> bool:
    return _is_join_channel_step_chunk(chunk) or _token_auth_chunk_has_join_flow(chunk)


def _chunk_has_preferred_generic_join_step(chunk: RetrievedChunk) -> bool:
    return _is_preferred_generic_join_step_chunk(chunk) or _token_auth_chunk_has_join_flow(chunk)


def _is_audio_video_calling_core_chunk(chunk: RetrievedChunk) -> bool:
    return _chunk_product(chunk) in _AUDIO_VIDEO_CALLING_CORE_PRODUCTS


def _is_audio_video_calling_secondary_chunk(chunk: RetrievedChunk) -> bool:
    return _chunk_product(chunk) in _AUDIO_VIDEO_CALLING_SECONDARY_PRODUCTS


def _generic_join_product_compatible(chunk: RetrievedChunk, product: str | None) -> bool:
    normalized_product = _normalized_query_text(product)
    chunk_product = str(_chunk_product(chunk) or "").strip().lower()
    if normalized_product != SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING:
        return chunk_product not in _AUDIO_VIDEO_CALLING_PENALIZED_PRODUCTS
    if chunk_product in _AUDIO_VIDEO_CALLING_CORE_PRODUCTS:
        return True
    if not chunk_product:
        # Older or lightly normalized docs may not carry explicit product metadata.
        # For generic join/auth guidance, treat those unlabeled chunks as compatible
        # unless another rule later excludes them as stream/multi-channel specific.
        return True
    return False


def _generic_join_support_signals(
    chunks: list[RetrievedChunk],
    *,
    product: str | None,
    query: str | None = None,
) -> tuple[bool, bool]:
    compatible_chunks = [chunk for chunk in chunks if _generic_join_product_compatible(chunk, product)]
    has_join_step = any(_chunk_covers_generic_join_step(chunk) for chunk in compatible_chunks)
    has_token_auth = any(_chunk_covers_generic_join_auth_prerequisite(chunk) for chunk in compatible_chunks)
    return has_join_step, has_token_auth


def _generic_join_has_preferred_join_step(
    chunks: list[RetrievedChunk],
    *,
    product: str | None,
    query: str | None = None,
) -> bool:
    compatible_chunks = [chunk for chunk in chunks if _generic_join_product_compatible(chunk, product)]
    require_preferred_join_step = _generic_join_requires_role_agnostic_guidance(query or "")
    if not require_preferred_join_step:
        return any(_chunk_covers_generic_join_step(chunk) for chunk in compatible_chunks)
    return any(_chunk_has_preferred_generic_join_step(chunk) for chunk in compatible_chunks)


def _product_affinity_adjustment(chunk: RetrievedChunk, product: str | None) -> tuple[float, list[str]]:
    normalized_product = _normalized_query_text(product)
    if normalized_product != SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING:
        return 0.0, []
    chunk_product = _chunk_product(chunk)
    reasons: list[str] = []
    boost = 0.0
    if chunk_product in _AUDIO_VIDEO_CALLING_CORE_PRODUCTS:
        boost += 0.9
        reasons.append(f"product_affinity:{chunk_product}")
    elif chunk_product in _AUDIO_VIDEO_CALLING_SECONDARY_PRODUCTS:
        boost += 0.2
        reasons.append(f"product_affinity_secondary:{chunk_product}")
    if chunk_product in _AUDIO_VIDEO_CALLING_PENALIZED_PRODUCTS:
        boost -= 1.0
        reasons.append(f"product_penalty:{chunk_product}")
    return boost, reasons


def _join_intent_adjustment(query: str, chunk: RetrievedChunk, product: str | None) -> tuple[float, list[str]]:
    reasons: list[str] = []
    boost = 0.0
    if _is_generic_join_channel_query(query):
        if _generic_join_requires_role_agnostic_guidance(query) and _is_role_specific_join_chunk(chunk):
            boost -= 1.35
            reasons.append("intent:generic_join_role_specific_penalty")
        if (
            _normalized_query_text(product) == SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING
            and _is_audio_video_calling_secondary_chunk(chunk)
        ):
            boost -= 1.15
            reasons.append("intent:generic_join_secondary_product_penalty")
        if _chunk_covers_generic_join_step(chunk):
            boost += 1.55
            reasons.append("intent:join_channel_step")
            if _chunk_has_preferred_generic_join_step(chunk):
                boost += 0.45
                reasons.append("intent:join_channel_preferred_step")
        if _is_token_auth_chunk(chunk):
            boost += 1.35
            reasons.append("intent:join_channel_token_auth")
            if _token_auth_chunk_has_join_flow(chunk):
                boost += 0.6
                reasons.append("intent:join_channel_token_auth_join_flow")
        if _is_join_multiple_channels_chunk(chunk):
            boost -= 1.9
            reasons.append("intent:generic_join_multiple_penalty")
        if _is_stream_channel_chunk(chunk):
            boost -= 2.0
            reasons.append("intent:generic_join_stream_penalty")
        return boost, reasons
    if _is_multiple_channels_query(query) and _is_join_multiple_channels_chunk(chunk):
        return 1.0, ["intent:multiple_channels"]
    if _is_stream_channel_query(query) and _is_stream_channel_chunk(chunk):
        return 1.85, ["intent:stream_channel"]
    return 0.0, []


def _generic_join_supporting_chunks(chunks: list[RetrievedChunk], *, product: str | None = None) -> list[RetrievedChunk]:
    preferred: list[RetrievedChunk] = []
    seen_ids: set[str] = set()
    for matcher in (_chunk_covers_generic_join_step, _chunk_covers_generic_join_auth_prerequisite):
        for chunk in chunks:
            chunk_id = str(chunk.chunk_id or "")
            if chunk_id and chunk_id in seen_ids:
                continue
            if not matcher(chunk):
                continue
            if not _generic_join_product_compatible(chunk, product):
                continue
            preferred.append(chunk)
            if chunk_id:
                seen_ids.add(chunk_id)
            break
    return preferred


def _select_generic_join_grounding_chunks(
    chunks: list[RetrievedChunk],
    *,
    product: str | None,
    query: str | None,
) -> tuple[RetrievedChunk | None, RetrievedChunk | None]:
    compatible_chunks = [chunk for chunk in chunks if _generic_join_product_compatible(chunk, product)]
    require_preferred_join_step = _generic_join_requires_role_agnostic_guidance(query or "")
    join_chunk: RetrievedChunk | None = next(
        (
            chunk
            for chunk in compatible_chunks
            if (
                _chunk_has_preferred_generic_join_step(chunk)
                if require_preferred_join_step
                else _chunk_covers_generic_join_step(chunk)
            )
        ),
        None,
    )
    if join_chunk is None and require_preferred_join_step:
        join_chunk = next((chunk for chunk in compatible_chunks if _chunk_covers_generic_join_step(chunk)), None)
    auth_chunk = next((chunk for chunk in compatible_chunks if _is_token_auth_chunk(chunk)), None)
    return join_chunk, auth_chunk


def _generic_join_options_term(join_chunk: RetrievedChunk | None) -> str:
    if join_chunk is not None and "channelmediaoptions" in _chunk_surface_text(join_chunk):
        return "`ChannelMediaOptions`"
    return "channel/media options"


def _extract_authoritative_code_block(chunk: RetrievedChunk | None) -> str | None:
    if chunk is None:
        return None
    for code_block, _body in _iter_authoritative_code_blocks(chunk):
        return code_block
    return None


def _iter_authoritative_code_blocks(chunk: RetrievedChunk | None) -> Iterable[tuple[str, str]]:
    if chunk is None:
        return
    for match in re.finditer(r"```([A-Za-z0-9_+-]*)\n(.*?)```", str(chunk.text or ""), flags=re.DOTALL):
        language = str(match.group(1) or "").strip()
        body = str(match.group(2) or "").strip("\n")
        if not body:
            continue
        fence = f"```{language}" if language else "```"
        yield f"{fence}\n{body}\n```", body


def _generic_join_code_block_score(body: str) -> int:
    text = str(body or "").lower()
    compact = re.sub(r"\s+", "", text)
    if not text.strip():
        return 0
    score = 0
    if "joinchannel(" in compact:
        score += 4
    elif re.search(r"\bjoin\s*\(", text):
        score += 2
    if score <= 0:
        return 0
    if any(marker in text for marker in ["channel", "channelid", "channel name"]):
        score += 1
    if any(marker in text for marker in ["token", "uid", "user id", "userid"]):
        score += 1
    return score


def _extract_generic_join_code_block(*chunks: RetrievedChunk | None) -> str | None:
    best_block: str | None = None
    best_score = 0
    for chunk in chunks:
        for code_block, body in _iter_authoritative_code_blocks(chunk):
            score = _generic_join_code_block_score(body)
            if score > best_score:
                best_block = code_block
                best_score = score
    return best_block


def _answer_has_fenced_code_block(value: str) -> bool:
    return bool(re.search(r"```[A-Za-z0-9_+-]*\s*\n.*?```", str(value or ""), flags=re.DOTALL))


def _deduped_chunk_order_for_code_example(
    chunks: list[RetrievedChunk],
    citation_ids: list[str],
) -> list[RetrievedChunk]:
    chunk_by_id = {str(chunk.chunk_id): chunk for chunk in chunks if str(chunk.chunk_id or "").strip()}
    ordered: list[RetrievedChunk] = []
    seen: set[str] = set()
    for chunk_id in citation_ids:
        chunk = chunk_by_id.get(str(chunk_id))
        if chunk is None:
            continue
        dedupe_key = _chunk_dedupe_key(chunk)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        ordered.append(chunk)
    for chunk in chunks:
        dedupe_key = _chunk_dedupe_key(chunk)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        ordered.append(chunk)
    return ordered


def _supplement_how_to_code_example_if_missing(
    answer: str,
    *,
    question: str,
    chunks: list[RetrievedChunk],
    citation_ids: list[str],
) -> tuple[str, list[str]]:
    body = str(answer or "").strip()
    normalized_citation_ids = [str(chunk_id) for chunk_id in citation_ids if str(chunk_id or "").strip()]
    if not body or not (is_answer_first_how_to_message(question) or _is_how_to_faq_query(question)):
        return body, normalized_citation_ids
    if _answer_has_fenced_code_block(body):
        return body, normalized_citation_ids
    for chunk in _deduped_chunk_order_for_code_example(chunks, normalized_citation_ids):
        code_block = _extract_authoritative_code_block(chunk)
        if not code_block:
            continue
        chunk_id = str(chunk.chunk_id or "").strip()
        updated_citation_ids = list(normalized_citation_ids)
        if chunk_id and chunk_id not in updated_citation_ids:
            updated_citation_ids.append(chunk_id)
        return f"{body}\n\nReference Example:\n{code_block}", updated_citation_ids
    return body, normalized_citation_ids


def _build_black_screen_guidance_grounded_answer(
    message: str,
    chunks: list[RetrievedChunk],
    *,
    product: str | None,
    requester: str | None = None,
    customer_id: str | None = None,
) -> RagAnswer | None:
    if not _allows_release_note_guidance_for_short_symptom_query(message):
        return None
    release_note_chunk, faq_chunk = _select_black_screen_guidance_chunks(
        chunks,
        product=product,
    )
    if release_note_chunk is None or faq_chunk is None:
        return None
    cited_chunks = [release_note_chunk]
    if _chunk_dedupe_key(faq_chunk) != _chunk_dedupe_key(release_note_chunk):
        cited_chunks.append(faq_chunk)
    body = (
        "If you're seeing a black screen, first review the available Agora release notes for known black-screen fixes "
        "and update to an SDK version that includes them."
    )
    steps = [
        "If this is a Web SDK case, check the release notes for the listed black-screen fixes and upgrade to a version that includes them.",
        'Review the Quickstart FAQ entry "How can I fix black screen issues?" for your SDK or app type and apply the recommended checks there.',
        "If the issue continues, please share the channel name, problematic uid, and issue timestamp so the investigation can be narrowed down.",
    ]
    citation_records = _citation_records_from_chunks(cited_chunks, limit=len(cited_chunks))
    sources = [
        record.get("source_url") or f"rag:{record['chunk_id']}"
        for record in citation_records
    ]
    confidence = max(0.88, _confidence_from_chunks(cited_chunks))
    return RagAnswer(
        answer=_compose_grounded_answer_email(
            question=message,
            body=body,
            steps=steps,
            requester=requester,
            customer_id=customer_id,
        ),
        confidence=round(min(0.95, confidence), 2),
        sources=sources,
        citations=citation_records,
    )


def _build_generic_join_grounded_answer(
    message: str,
    chunks: list[RetrievedChunk],
    *,
    product: str | None,
    requester: str | None = None,
    customer_id: str | None = None,
) -> RagAnswer | None:
    if not _is_generic_join_channel_query(message):
        return None
    join_chunk, auth_chunk = _select_generic_join_grounding_chunks(
        chunks,
        product=product,
        query=message,
    )
    join_chunk_covers_auth = join_chunk is not None and _chunk_covers_generic_join_auth_prerequisite(join_chunk)
    if join_chunk is None or (auth_chunk is None and not join_chunk_covers_auth):
        return None
    options_term = _generic_join_options_term(join_chunk)
    token_step = (
        "2. Pass a valid authentication token; in production, get it from your token server, or use a temporary token for testing.\n"
        if auth_chunk is not None
        else "2. Pass a valid authentication token before joining the channel.\n"
    )
    cited_chunks = [join_chunk]
    if auth_chunk is not None and _chunk_dedupe_key(auth_chunk) != _chunk_dedupe_key(join_chunk):
        cited_chunks.append(auth_chunk)
    example_code_block = _extract_generic_join_code_block(join_chunk, auth_chunk)
    body = (
        "To join a channel, call the SDK join method with your channel name, authentication token, "
        f"user ID, and {options_term}. The channel name identifies which channel to join, the token "
        "authenticates the user, and the user ID identifies the local user before joining."
    )
    steps = [
        "Provide the channel name you want the client to join.",
        token_step.strip(),
        "Set the local user ID.",
        f"Configure {options_term} as needed, then call the SDK join method.",
    ]
    if example_code_block:
        body = f"{body}\n\nReference Example:\n{example_code_block}"
    citation_records = _citation_records_from_chunks(cited_chunks, limit=len(cited_chunks))
    sources = [
        record.get("source_url") or f"rag:{record['chunk_id']}"
        for record in citation_records
    ]
    confidence = max(0.88, _confidence_from_chunks(cited_chunks))
    return RagAnswer(
        answer=_compose_grounded_answer_email(
            question=message,
            body=body,
            steps=steps,
            requester=requester,
            customer_id=customer_id,
        ),
        confidence=round(min(0.96, confidence), 2),
        sources=sources,
        citations=citation_records,
    )


def _enforce_generic_join_support_pair(
    chunks: list[RetrievedChunk],
    *,
    reranked_chunks: list[RetrievedChunk],
    product: str | None,
    query: str | None,
    limit: int,
) -> list[RetrievedChunk]:
    if not _is_generic_join_channel_query(query or ""):
        return list(chunks)
    top_focus_chunk = chunks[0] if chunks else None
    compatible_chunks = [chunk for chunk in chunks if _generic_join_product_compatible(chunk, product)]
    require_preferred_join_step = _generic_join_requires_role_agnostic_guidance(query or "")
    has_preferred_join_step = any(
        (_chunk_has_preferred_generic_join_step(chunk) if require_preferred_join_step else _chunk_covers_generic_join_step(chunk))
        for chunk in compatible_chunks
    )
    has_any_join_step = any(_chunk_covers_generic_join_step(chunk) for chunk in compatible_chunks)
    has_token_auth = any(_chunk_covers_generic_join_auth_prerequisite(chunk) for chunk in compatible_chunks)
    if (
        has_preferred_join_step
        and has_token_auth
        and not (
            top_focus_chunk is not None
            and (_is_join_multiple_channels_chunk(top_focus_chunk) or _is_stream_channel_chunk(top_focus_chunk))
        )
    ):
        return list(chunks)
    if (
        has_any_join_step
        and has_token_auth
        and not require_preferred_join_step
        and not (
            top_focus_chunk is not None
            and (_is_join_multiple_channels_chunk(top_focus_chunk) or _is_stream_channel_chunk(top_focus_chunk))
        )
    ):
        return list(chunks)
    supporting_chunks = _generic_join_supporting_chunks(reranked_chunks, product=product)
    if len(supporting_chunks) < 2:
        return list(chunks)
    merged: list[RetrievedChunk] = []
    seen: set[str] = set()
    safe_limit = max(1, int(limit or 1))
    for chunk in ([_copy_chunk(item) for item in supporting_chunks] + [_copy_chunk(item) for item in chunks]):
        dedupe_key = _chunk_dedupe_key(chunk)
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        merged.append(chunk)
        if len(merged) >= safe_limit:
            break
    return merged or list(chunks)


def _requires_howto_citation_retry(
    *,
    message: str,
    product: str | None,
    chunks: list[RetrievedChunk],
    payload: dict[str, Any] | None,
) -> bool:
    if not _is_generic_join_channel_query(message):
        return False
    if not isinstance(payload, dict) or payload.get("insufficient_evidence") is True:
        return False
    citations = payload.get("citations")
    if not isinstance(citations, list) or len(citations) >= 2:
        return False
    return len(_generic_join_supporting_chunks(chunks, product=product)) >= 2


def _is_release_note_lookup_query(query: str) -> bool:
    normalized = _normalized_query_text(query)
    if not normalized:
        return False
    release_markers = [
        "release note",
        "release notes",
        "what changed",
        "fixed in",
        "bug fix",
        "issues fixed",
        "known issue",
        "sdk version",
        "version ",
        "changelog",
    ]
    return any(marker in normalized for marker in release_markers)


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
    if not profile_has_invocation_credentials(profile):
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
    shadow_retrieval_enabled: bool | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> AgenticRetrievalPlan:
    if shadow_retrieval_enabled is None:
        shadow_retrieval_enabled = _shadow_retrieval_enabled()
    query_class = _classify_agentic_query(message, query_understanding)
    normalized_message = " ".join(str(message or "").split()).strip()
    short_faq_pattern = _short_lexical_faq_pattern(message)
    exact_terms = (
        _short_lexical_faq_exact_terms(message)
        if short_faq_pattern is not None
        else _extract_query_terms(message, max_terms=6)
    )
    if query_class == "lexical_exact" and _is_simple_lexical_query(message):
        return AgenticRetrievalPlan(
            query_class=query_class,
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", normalized_message)],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=bool(ticket_context),
            exact_terms=exact_terms,
            light_path=True,
            product=product,
            shadow_tools_skipped=[],
        )
    if query_class == "usage_configuration" and _is_short_how_to_faq_query(message, query_understanding):
        return AgenticRetrievalPlan(
            query_class=query_class,
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=_usage_configuration_query_variants(normalized_message, query_understanding),
            decomposition_targets=[],
            evidence_goal="how_to_usage_support",
            recovery_bias="lexical",
            ticket_context_used=bool(ticket_context),
            exact_terms=exact_terms,
            light_path=True,
            product=product,
            shadow_tools_skipped=[],
        )
    if short_faq_pattern in {"token_usage", "connection_state"}:
        return AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", normalized_message)],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=bool(ticket_context),
            exact_terms=exact_terms,
            light_path=True,
            product=product,
            shadow_tools_skipped=[],
        )
    if query_class == "api_semantics_mismatch":
        return AgenticRetrievalPlan(
            query_class=query_class,
            first_pass_tools=["p_bm25"],
            query_variants=[("original", normalized_message)],
            decomposition_targets=[],
            evidence_goal="api_semantics_grounding",
            recovery_bias="lexical",
            ticket_context_used=bool(ticket_context),
            exact_terms=exact_terms,
            light_path=True,
            product=product,
            shadow_tools_skipped=[],
        )
    if query_class == "usage_configuration":
        return AgenticRetrievalPlan(
            query_class=query_class,
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=_usage_configuration_query_variants(normalized_message, query_understanding),
            decomposition_targets=[],
            evidence_goal="configuration_support",
            recovery_bias="lexical",
            ticket_context_used=bool(ticket_context),
            exact_terms=exact_terms,
            light_path=False,
            product=product,
            shadow_tools_skipped=[],
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
        if query_class in {"how_to_faq", "configuration"}:
            query_class = "usage_configuration"
        if query_class in {"lexical_exact", "usage_configuration", "unclear_query", "troubleshooting_why", "comparison"}:
            first_pass_tools, planner_shadow_skipped = _filter_shadow_tool_names(
                [
                    str(item).strip()
                    for item in planner_payload.get("first_pass_tools") or []
                    if str(item).strip()
                ],
                shadow_retrieval_enabled=shadow_retrieval_enabled,
            )
            raw_default_tool_order, default_evidence_goal, default_recovery_bias = _raw_tool_order_for_query_class(
                query_class
            )
            default_tool_order, default_shadow_skipped = _filter_shadow_tool_names(
                raw_default_tool_order,
                shadow_retrieval_enabled=shadow_retrieval_enabled,
            )
            query_variants = [
                (str(item[0]).strip(), str(item[1]).strip())
                for item in planner_payload.get("query_variants") or []
                if isinstance(item, (list, tuple)) and len(item) >= 2 and str(item[1]).strip()
            ]
            if query_variants:
                return AgenticRetrievalPlan(
                    query_class=query_class,
                    first_pass_tools=first_pass_tools or list(default_tool_order),
                    query_variants=query_variants,
                    decomposition_targets=[
                        str(item).strip()
                        for item in planner_payload.get("decomposition_targets") or []
                        if str(item).strip()
                    ],
                    evidence_goal=str(planner_payload.get("evidence_goal") or default_evidence_goal).strip(),
                    recovery_bias=str(planner_payload.get("recovery_bias") or default_recovery_bias).strip(),
                    ticket_context_used=bool(ticket_context),
                    exact_terms=exact_terms,
                    light_path=False,
                    product=product,
                    shadow_tools_skipped=(
                        list(planner_shadow_skipped)
                        if first_pass_tools
                        else list(default_shadow_skipped)
                    ),
                )

    raw_tool_order, evidence_goal, recovery_bias = _raw_tool_order_for_query_class(query_class)
    tool_order, default_shadow_skipped = _filter_shadow_tool_names(
        raw_tool_order,
        shadow_retrieval_enabled=shadow_retrieval_enabled,
    )
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
        exact_terms=exact_terms,
        light_path=False,
        product=product,
        shadow_tools_skipped=list(default_shadow_skipped),
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


def _api_semantics_has_request_parameter_support(message: str, chunks: list[RetrievedChunk]) -> bool:
    parameter_groups = _api_semantics_parameter_groups(message)
    if not parameter_groups:
        return True
    for parameter_group in parameter_groups:
        if not any(_is_api_semantics_request_parameters_chunk(chunk, required_terms=parameter_group) for chunk in chunks):
            return False
    return True


def _api_semantics_parameter_groups(message: str) -> list[set[str]]:
    parameter_terms = {
        term.lower()
        for term in extract_anchor_hits(message)
        if term.lower() in {"uid", "str_uid", "time", "time_in_seconds", "cname", "ip"}
    }
    groups: list[set[str]] = []
    if {"uid", "str_uid"} & parameter_terms:
        groups.append({"uid", "str_uid"})
    if {"time", "time_in_seconds"} & parameter_terms:
        groups.append({"time", "time_in_seconds"})
    if "cname" in parameter_terms:
        groups.append({"cname"})
    if "ip" in parameter_terms:
        groups.append({"ip"})
    return groups


def _api_semantics_prefers_disband_chunk(message: str) -> bool:
    lowered = str(message or "").lower()
    return "disband" in lowered and "channel" in lowered


def _is_api_semantics_disband_chunk(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    section_path = " > ".join(_chunk_metadata_list(metadata.get("section_path"))).lower()
    source_url_lower = str(chunk.source_url or "").strip().lower()
    source_path_lower = str(chunk.source_path or "").strip().lower()
    return (
        "disband a channel" in section_path
        or "disband-a-channel" in source_url_lower
        or ("ban-user-privileges" in source_path_lower and "disband a channel" in _chunk_search_text(chunk))
    )


def _is_api_semantics_request_parameters_chunk(
    chunk: RetrievedChunk,
    *,
    required_terms: set[str] | None = None,
) -> bool:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    chunk_type = str(metadata.get("chunk_type") or "").strip().lower()
    section_path = " > ".join(_chunk_metadata_list(metadata.get("section_path"))).lower()
    source_url_lower = str(chunk.source_url or "").strip().lower()
    source_path_lower = str(chunk.source_path or "").strip().lower()
    search_text = _chunk_search_text(chunk)
    if "create-rules" not in source_url_lower and "create-rules" not in source_path_lower:
        return False
    if "response parameters" in section_path:
        return False
    if "request parameters" not in section_path and chunk_type != "api_params":
        return False
    if not required_terms:
        return True
    return any(term in search_text for term in required_terms)


def _api_semantics_product_hints(message: str) -> list[str]:
    anchor_hits = {item.lower() for item in extract_anchor_hits(message)}
    products: list[str] = []
    for slug in ["broadcast-streaming", "video-calling", "voice-calling", "interactive-live-streaming"]:
        if slug in anchor_hits or slug in str(message or "").lower():
            products.append(slug)
    return products


def _pinned_chunk_from_row(
    row: tuple[Any, ...],
    *,
    index_role: str,
    retrieval_source: str,
) -> RetrievedChunk:
    return RetrievedChunk(
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
        similarity=1.0,
        index_role=index_role,
        retrieval_sources=[retrieval_source],
        candidate_trace={
            "raw_score": 1.0,
            "source_pinned": True,
            "index_role": index_role,
        },
    )


def _fetch_api_semantics_pinned_chunks(
    *,
    message: str,
    config: dict[str, Any],
    existing_chunks: list[RetrievedChunk],
    index_role: str = "primary",
) -> list[RetrievedChunk]:
    product_hints = _api_semantics_product_hints(message)
    needs_disband = _api_semantics_prefers_disband_chunk(message) and not any(
        _is_api_semantics_disband_chunk(chunk) for chunk in existing_chunks
    )
    required_parameter_groups = [
        group
        for group in _api_semantics_parameter_groups(message)
        if not any(_is_api_semantics_request_parameters_chunk(chunk, required_terms=group) for chunk in existing_chunks)
    ]
    if not needs_disband and not required_parameter_groups:
        return []

    psycopg = _import_psycopg()
    sql = psycopg.sql
    normalized_index_role = str(index_role or "").strip().lower() or "primary"
    like_product_patterns = [f"%{slug}%" for slug in product_hints] or ["%broadcast-streaming%"]

    def _fetch_one(
        *,
        retrieval_source: str,
        url_pattern: str,
        h3_pattern: str | None = None,
        content_patterns: list[str] | None = None,
    ) -> RetrievedChunk | None:
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
            WHERE index_role = %s
              AND lower(coalesce(source_url, '')) LIKE %s
              AND (%s::text IS NULL OR lower(coalesce(h3, '')) LIKE %s::text)
              AND (
                    %s::text[] IS NULL
                    OR lower(coalesce(content, '')) LIKE ANY(%s::text[])
                  )
            ORDER BY
              CASE
                WHEN EXISTS (
                  SELECT 1
                  FROM unnest(%s::text[]) AS product(pattern)
                  WHERE lower(coalesce(source_url, '')) LIKE product.pattern
                ) THEN 0
                ELSE 1
              END,
              CASE
                WHEN lower(coalesce(metadata ->> 'product', '')) = ANY(%s::text[]) THEN 0
                ELSE 1
              END,
              id
            LIMIT 1
            """
        ).format(_table_identifier(sql, config["table"]))
        normalized_product_hints = [slug.lower() for slug in product_hints]
        content_terms = content_patterns or None
        with psycopg.connect(config["dsn"]) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        normalized_index_role,
                        url_pattern.lower(),
                        h3_pattern.lower() if h3_pattern else None,
                        h3_pattern.lower() if h3_pattern else None,
                        content_terms,
                        content_terms,
                        like_product_patterns,
                        normalized_product_hints,
                    ),
                )
                row = cur.fetchone()
        if row is None:
            return None
        chunk = _pinned_chunk_from_row(
            row,
            index_role=normalized_index_role,
            retrieval_source=retrieval_source,
        )
        chunk.rerank_reasons = [f"api_semantics:source_pinned:{retrieval_source}"]
        chunk.rerank_score = 7.2
        return chunk

    pinned_chunks: list[RetrievedChunk] = []
    if needs_disband:
        disband_chunk = _fetch_one(
            retrieval_source="api_semantics_disband_pinned",
            url_pattern="%ban-user-privileges%",
            h3_pattern="%disband a channel%",
        )
        if disband_chunk is not None:
            pinned_chunks.append(disband_chunk)

    for parameter_group in required_parameter_groups:
        content_patterns = [f"%{term}%" for term in sorted(parameter_group)]
        parameter_chunk = _fetch_one(
            retrieval_source="api_semantics_request_params_pinned",
            url_pattern="%create-rules%",
            h3_pattern="%request parameters%",
            content_patterns=content_patterns,
        )
        if parameter_chunk is not None:
            pinned_chunks.append(parameter_chunk)

    deduped: dict[str, RetrievedChunk] = {}
    for chunk in pinned_chunks:
        deduped[_chunk_dedupe_key(chunk)] = chunk
    return list(deduped.values())


def _prepend_api_semantics_pinned_chunks(
    *,
    message: str,
    chunks: list[RetrievedChunk],
    pinned_chunks: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    if not pinned_chunks:
        return chunks
    merged: dict[str, RetrievedChunk] = {}
    ordered: list[RetrievedChunk] = []
    for chunk in [*pinned_chunks, *chunks]:
        dedupe_key = _chunk_dedupe_key(chunk)
        existing = merged.get(dedupe_key)
        if existing is None:
            copied = _copy_chunk(chunk)
            if any(source.startswith("api_semantics_") for source in copied.retrieval_sources):
                copied.rerank_score = max(float(copied.rerank_score or 0.0), 7.2)
            merged[dedupe_key] = copied
            ordered.append(copied)
            continue
        existing.retrieval_sources = list(dict.fromkeys([*existing.retrieval_sources, *chunk.retrieval_sources]))
        if chunk.rerank_reasons:
            existing.rerank_reasons = list(dict.fromkeys([*existing.rerank_reasons, *chunk.rerank_reasons]))
        if any(source.startswith("api_semantics_") for source in chunk.retrieval_sources):
            existing.rerank_score = max(float(existing.rerank_score or 0.0), float(chunk.rerank_score or 7.2))
    return _reorder_chunks_for_rerank(ordered, limit=len(ordered), query=message) or ordered


def _generic_join_product_hints(product: str | None) -> list[str]:
    normalized_product = _normalized_query_text(product)
    if normalized_product == SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING:
        return ["video-calling", "voice-calling"]
    if normalized_product:
        return [normalized_product]
    return ["video-calling", "voice-calling"]


def _fetch_generic_join_pinned_chunks(
    *,
    message: str,
    config: dict[str, Any],
    existing_chunks: list[RetrievedChunk],
    product: str | None,
    index_role: str = "primary",
) -> list[RetrievedChunk]:
    if not _is_generic_join_channel_query(message):
        return []

    require_preferred_join_step = _generic_join_requires_role_agnostic_guidance(message)
    compatible_existing_chunks = [
        chunk for chunk in existing_chunks if _generic_join_product_compatible(chunk, product)
    ]
    has_join_step, has_token_auth = _generic_join_support_signals(
        compatible_existing_chunks,
        product=product,
        query=message,
    )
    top_chunk = compatible_existing_chunks[0] if compatible_existing_chunks else None
    top_wrong_family = top_chunk is not None and (
        _is_join_multiple_channels_chunk(top_chunk) or _is_stream_channel_chunk(top_chunk)
    )
    needs_join_chunk = not has_join_step or top_wrong_family
    needs_token_auth_chunk = not has_token_auth
    if not needs_join_chunk and not needs_token_auth_chunk:
        return []

    try:
        psycopg = _import_psycopg()
        sql = psycopg.sql
        normalized_index_role = str(index_role or "").strip().lower() or "primary"
        product_hints = _generic_join_product_hints(product)
        product_patterns = [f"%{hint}%" for hint in product_hints]

        def _fetch_one(
            *,
            retrieval_source: str,
            url_patterns: list[str],
            path_patterns: list[str],
            heading_patterns: list[str],
            content_patterns: list[str] | None = None,
        ) -> RetrievedChunk | None:
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
                WHERE index_role = %s
                  AND (
                        lower(coalesce(source_url, '')) LIKE ANY(%s::text[])
                        OR lower(coalesce(source_path, '')) LIKE ANY(%s::text[])
                      )
                  AND (
                        %s::text[] IS NULL
                        OR lower(coalesce(h1, '')) LIKE ANY(%s::text[])
                        OR lower(coalesce(h2, '')) LIKE ANY(%s::text[])
                        OR lower(coalesce(h3, '')) LIKE ANY(%s::text[])
                      )
                  AND (
                        %s::text[] IS NULL
                        OR lower(coalesce(content, '')) LIKE ANY(%s::text[])
                      )
                ORDER BY
                  CASE
                    WHEN lower(coalesce(metadata ->> 'product', '')) = ANY(%s::text[]) THEN 0
                    ELSE 1
                  END,
                  CASE
                    WHEN EXISTS (
                      SELECT 1
                      FROM unnest(%s::text[]) AS product(pattern)
                      WHERE lower(coalesce(source_url, '')) LIKE product.pattern
                         OR lower(coalesce(source_path, '')) LIKE product.pattern
                    ) THEN 0
                    ELSE 1
                  END,
                  id
                LIMIT 1
                """
            ).format(_table_identifier(sql, config["table"]))
            normalized_heading_patterns = [item.lower() for item in heading_patterns] or None
            normalized_content_patterns = [item.lower() for item in content_patterns] if content_patterns else None
            normalized_product_hints = [hint.lower() for hint in product_hints]
            with psycopg.connect(config["dsn"]) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (
                            normalized_index_role,
                            [item.lower() for item in url_patterns],
                            [item.lower() for item in path_patterns],
                            normalized_heading_patterns,
                            normalized_heading_patterns,
                            normalized_heading_patterns,
                            normalized_heading_patterns,
                            normalized_content_patterns,
                            normalized_content_patterns,
                            normalized_product_hints,
                            product_patterns,
                        ),
                    )
                    row = cur.fetchone()
            if row is None:
                return None
            chunk = _pinned_chunk_from_row(
                row,
                index_role=normalized_index_role,
                retrieval_source=retrieval_source,
            )
            chunk.rerank_reasons = [f"generic_join:source_pinned:{retrieval_source}"]
            chunk.rerank_score = 7.0
            return chunk

        pinned_chunks: list[RetrievedChunk] = []
        if needs_join_chunk:
            join_chunk = _fetch_one(
                retrieval_source="generic_join_join_step_pinned",
                url_patterns=["%video-calling%", "%voice-calling%"],
                path_patterns=["%get-started-sdk%"],
                heading_patterns=["%join a channel%", "%implement video calling%", "%quickstart%"],
                content_patterns=["%joinchannel%", "%join a channel%", "%channel name%"],
            )
            if join_chunk is not None and (
                _chunk_has_preferred_generic_join_step(join_chunk)
                if require_preferred_join_step
                else _chunk_covers_generic_join_step(join_chunk)
            ):
                pinned_chunks.append(join_chunk)
        if needs_token_auth_chunk:
            auth_chunk = _fetch_one(
                retrieval_source="generic_join_token_auth_pinned",
                url_patterns=["%authentication-workflow%", "%token-authentication%"],
                path_patterns=["%authentication-workflow%"],
                heading_patterns=["%use a token to join a channel%", "%implement basic authentication%"],
                content_patterns=["%request a token%", "%join a channel%", "%channel name%", "%user id%"],
            )
            if auth_chunk is not None and _is_token_auth_chunk(auth_chunk):
                pinned_chunks.append(auth_chunk)

        deduped: dict[str, RetrievedChunk] = {}
        for chunk in pinned_chunks:
            deduped[_chunk_dedupe_key(chunk)] = chunk
        return list(deduped.values())
    except Exception as exc:
        logger.warning("Generic join pinned chunk lookup failed: %s", exc)
        return []


def _prepend_generic_join_pinned_chunks(
    *,
    message: str,
    chunks: list[RetrievedChunk],
    pinned_chunks: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    if not pinned_chunks:
        return chunks
    merged: dict[str, RetrievedChunk] = {}
    ordered: list[RetrievedChunk] = []
    for chunk in [*pinned_chunks, *chunks]:
        dedupe_key = _chunk_dedupe_key(chunk)
        existing = merged.get(dedupe_key)
        if existing is None:
            copied = _copy_chunk(chunk)
            if any(source.startswith("generic_join_") for source in copied.retrieval_sources):
                copied.rerank_score = max(float(copied.rerank_score or 0.0), 7.0)
            merged[dedupe_key] = copied
            ordered.append(copied)
            continue
        existing.retrieval_sources = list(dict.fromkeys([*existing.retrieval_sources, *chunk.retrieval_sources]))
        if chunk.rerank_reasons:
            existing.rerank_reasons = list(dict.fromkeys([*existing.rerank_reasons, *chunk.rerank_reasons]))
        if any(source.startswith("generic_join_") for source in chunk.retrieval_sources):
            existing.rerank_score = max(float(existing.rerank_score or 0.0), float(chunk.rerank_score or 7.0))
    return _reorder_chunks_for_rerank(ordered, limit=len(ordered), query=message) or ordered


def _select_api_semantics_final_chunks(
    chunks: list[RetrievedChunk],
    *,
    limit: int,
    query: str,
) -> list[RetrievedChunk]:
    if not chunks:
        return []
    safe_limit = max(1, int(limit or 1))
    selected: list[RetrievedChunk] = []
    selected_keys: set[str] = set()

    def _add_first(predicate: Callable[[RetrievedChunk], bool]) -> None:
        if len(selected) >= safe_limit:
            return
        for chunk in chunks:
            key = _chunk_dedupe_key(chunk)
            if key in selected_keys:
                continue
            if not predicate(chunk):
                continue
            selected.append(chunk)
            selected_keys.add(key)
            break

    if _api_semantics_prefers_disband_chunk(query):
        _add_first(_is_api_semantics_disband_chunk)
    for parameter_group in _api_semantics_parameter_groups(query):
        _add_first(lambda chunk, group=parameter_group: _is_api_semantics_request_parameters_chunk(chunk, required_terms=group))
    for chunk in chunks:
        if len(selected) >= safe_limit:
            break
        key = _chunk_dedupe_key(chunk)
        if key in selected_keys:
            continue
        selected.append(chunk)
        selected_keys.add(key)
    return selected


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
    product: str | None = None,
    troubleshooting_recovery_unlikely: bool = False,
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
        if query_class == "api_semantics_mismatch" and not _api_semantics_has_request_parameter_support(message, final_chunks):
            return AgenticJudgeDecision("recover_once", "api_request_parameter_evidence_missing", 0.78, "lexical_recovery")
        if query_class == "comparison" and not comparison_covered:
            return AgenticJudgeDecision("recover_once", "comparison_targets_missing", 0.74, "compare_recovery")
        if query_class == "troubleshooting_why" and not reranked_chunks:
            return AgenticJudgeDecision("escalate", "missing_primary_support", 0.78, None)
        if (
            query_class == "troubleshooting_why"
            and _allows_release_note_guidance_for_short_symptom_query(message)
            and _has_black_screen_guidance_support(final_chunks, product=product)
        ):
            return AgenticJudgeDecision("answer_now", "sufficient_first_pass_support", 0.9, None)
        if (
            query_class == "troubleshooting_why"
            and top_chunk is not None
            and _is_release_note_chunk(top_chunk)
            and not _is_release_note_lookup_query(message)
            and not _allows_release_note_guidance_for_short_symptom_query(message)
        ):
            return AgenticJudgeDecision("escalate", "weak_top1_support", 0.82, None)
        if primary_count == 0:
            if query_class == "usage_configuration":
                recovery = "configuration_recovery"
            elif query_class in {"lexical_exact", "how_to_faq", "configuration"}:
                recovery = "lexical_recovery"
            else:
                recovery = "semantic_recovery"
            return AgenticJudgeDecision("recover_once", "missing_primary_support", 0.72, recovery)
        if query_class in {"lexical_exact", "how_to_faq", "usage_configuration"} and _is_generic_join_channel_query(message):
            top_focus_chunk = top_chunk or (final_chunks[0] if final_chunks else None)
            has_join_step, has_token_auth = _generic_join_support_signals(
                final_chunks,
                product=product,
                query=message,
            )
            has_preferred_join_step = _generic_join_has_preferred_join_step(
                final_chunks,
                product=product,
                query=message,
            )
            if (
                top_focus_chunk is not None
                and (_is_join_multiple_channels_chunk(top_focus_chunk) or _is_stream_channel_chunk(top_focus_chunk))
            ) or not has_join_step or not has_token_auth or not has_preferred_join_step:
                recovery = "configuration_recovery" if query_class == "usage_configuration" else "lexical_recovery"
                return AgenticJudgeDecision("recover_once", "generic_join_wrong_family", 0.78, recovery)
        if query_class == "lexical_exact" and (top_score < 0.32 or not exact_match_supported):
            return AgenticJudgeDecision("recover_once", "low_top1_rerank_score", 0.74, "lexical_recovery")
        if query_class == "troubleshooting_why" and troubleshooting_recovery_unlikely and top_score < 0.5:
            return AgenticJudgeDecision("escalate", "weak_top1_support", 0.82, None)
        if top_score < 0.32:
            if query_class == "troubleshooting_why" and troubleshooting_recovery_unlikely:
                return AgenticJudgeDecision("escalate", "weak_top1_support", 0.82, None)
            if query_class == "comparison":
                recovery = "compare_recovery"
            elif query_class == "usage_configuration":
                recovery = "configuration_recovery"
            elif query_class == "how_to_faq":
                recovery = "lexical_recovery"
            else:
                recovery = "semantic_recovery"
            return AgenticJudgeDecision("recover_once", "weak_first_pass_support", 0.7, recovery)
        return AgenticJudgeDecision("answer_now", "sufficient_first_pass_support", 0.9, None)

    if primary_count == 0:
        reason = "weak_shadow_only_support" if all_shadow else "missing_primary_support"
        return AgenticJudgeDecision("escalate", reason, 0.84, None)
    if top_score < 0.25:
        return AgenticJudgeDecision("escalate", "weak_top1_support", 0.82, None)
    if query_class == "comparison" and not comparison_covered:
        return AgenticJudgeDecision("escalate", "comparison_targets_missing", 0.84, None)
    if query_class in {"lexical_exact", "how_to_faq", "usage_configuration"} and _is_generic_join_channel_query(message):
        has_join_step, has_token_auth = _generic_join_support_signals(
            final_chunks,
            product=product,
            query=message,
        )
        top_focus_chunk = top_chunk or (final_chunks[0] if final_chunks else None)
        if (
            not has_join_step
            or not has_token_auth
            or (
                top_focus_chunk is not None
                and (_is_join_multiple_channels_chunk(top_focus_chunk) or _is_stream_channel_chunk(top_focus_chunk))
            )
        ):
            return AgenticJudgeDecision("escalate", "generic_join_support_incomplete", 0.84, None)
    if _same_family_only(final_chunks) and not grounded_overlap:
        return AgenticJudgeDecision("escalate", "single_family_ungrounded", 0.78, None)
    return AgenticJudgeDecision("answer_now", "sufficient_second_pass_support", 0.92, None)


def _troubleshooting_recovery_unlikely_from_timings(
    *,
    query_class: str,
    round_index: int,
    retrieval_tool_timings: list[dict[str, Any]],
) -> bool:
    if query_class != "troubleshooting_why" or round_index != 1:
        return False

    def _counts(*, family: str, query_kinds: set[str]) -> list[int]:
        counts: list[int] = []
        for item in retrieval_tool_timings:
            if not isinstance(item, dict):
                continue
            if _tool_family(str(item.get("tool_name") or "")) != family:
                continue
            if str(item.get("query_kind") or "") not in query_kinds:
                continue
            counts.append(int(item.get("candidate_count") or 0))
        return counts

    vector_expansion_counts = _counts(family="vector", query_kinds={"semantic", "rewrite", "context"})
    bm25_expansion_counts = _counts(family="bm25", query_kinds={"semantic", "rewrite", "context"})
    fts_original_counts = _counts(family="fts", query_kinds={"original"})
    if not vector_expansion_counts or not bm25_expansion_counts:
        return False
    if any(count > 0 for count in vector_expansion_counts):
        return False
    if any(count > 0 for count in bm25_expansion_counts):
        return False
    if fts_original_counts and any(count > 0 for count in fts_original_counts):
        return False
    return True


def _chunk_tool_family(chunk: RetrievedChunk) -> str:
    trace = chunk.candidate_trace if isinstance(chunk.candidate_trace, dict) else {}
    tool_name = str(trace.get("tool_name") or "").strip()
    if tool_name:
        return _tool_family(tool_name)
    for source in chunk.retrieval_sources:
        family = _tool_family(str(source or "").strip())
        if family in {"vector", "bm25", "fts", "keyword"}:
            return family
    return ""


def _troubleshooting_expansion_families_after_original(
    *,
    reranked_chunks: list[RetrievedChunk],
    final_chunks: list[RetrievedChunk],
) -> set[str]:
    def _support_score(chunk: RetrievedChunk | None) -> float:
        if chunk is None:
            return 0.0
        return float(
            (
                chunk.rerank_score
                if chunk.rerank_score is not None
                else chunk.similarity
            )
            or 0.0
        )

    top_chunk = reranked_chunks[0] if reranked_chunks else None
    top_score = _support_score(top_chunk)
    if top_chunk is not None and _is_release_note_chunk(top_chunk):
        return set()
    primary_supporting_chunks = [
        chunk
        for chunk in final_chunks
        if str(chunk.index_role or "").strip().lower() == "primary"
        and _chunk_tool_family(chunk) in {"vector", "bm25"}
        and _support_score(chunk) >= 0.64
        and not _is_release_note_chunk(chunk)
    ]
    lexical_supporting_chunks = [
        chunk
        for chunk in final_chunks
        if str(chunk.index_role or "").strip().lower() == "primary"
        and _chunk_tool_family(chunk) in {"bm25", "fts"}
        and _support_score(chunk) >= 0.66
        and not _is_release_note_chunk(chunk)
    ]
    vector_supporting_chunks = [
        chunk
        for chunk in final_chunks
        if str(chunk.index_role or "").strip().lower() == "primary"
        and _chunk_tool_family(chunk) == "vector"
        and _support_score(chunk) >= 0.74
        and not _is_release_note_chunk(chunk)
    ]
    if (
        top_score < 0.84
        or len(primary_supporting_chunks) < 2
        or not lexical_supporting_chunks
        or not vector_supporting_chunks
    ):
        return set()
    families: set[str] = set()
    for chunk in reranked_chunks[:2]:
        family = _chunk_tool_family(chunk)
        if family in {"vector", "bm25"} and _support_score(chunk) >= 0.72 and not _is_release_note_chunk(chunk):
            families.add(family)
    if families:
        return families
    for chunk in primary_supporting_chunks:
        family = _chunk_tool_family(chunk)
        if family in {"vector", "bm25"}:
            families.add(family)
    return families


def _troubleshooting_should_try_vector_after_lexical_support(
    *,
    message: str,
    reranked_chunks: list[RetrievedChunk],
    final_chunks: list[RetrievedChunk],
) -> bool:
    def _support_score(chunk: RetrievedChunk | None) -> float:
        if chunk is None:
            return 0.0
        return float(
            (
                chunk.rerank_score
                if chunk.rerank_score is not None
                else chunk.similarity
            )
            or 0.0
        )

    top_chunk = reranked_chunks[0] if reranked_chunks else None
    top_score = _support_score(top_chunk)
    if top_chunk is None or _is_release_note_chunk(top_chunk):
        return False
    lexical_supporting_chunks = [
        chunk
        for chunk in final_chunks
        if str(chunk.index_role or "").strip().lower() == "primary"
        and _chunk_tool_family(chunk) in {"bm25", "fts"}
        and _support_score(chunk) >= 0.48
        and not _is_release_note_chunk(chunk)
    ]
    if not lexical_supporting_chunks:
        return False
    if top_score >= 0.58:
        return True
    return _has_grounded_keyword_overlap(message, lexical_supporting_chunks) and top_score >= 0.42


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
    if query_class in {"how_to_faq", "usage_configuration"}:
        return {
            "p_bm25": 1.00,
            "p_fts": 0.90,
            "p_vec": 0.55,
            "s_bm25": 0.35,
            "s_fts": 0.25,
            "s_vec": 0.20,
            "p_keyword": 0.20,
            "s_keyword": 0.08,
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
    if query_class == "api_semantics_mismatch":
        return {
            "p_bm25": 1.00,
            "p_fts": 0.95,
            "p_vec": 0.55,
            "p_keyword": 0.20,
            "s_bm25": 0.0,
            "s_fts": 0.0,
            "s_vec": 0.0,
            "s_keyword": 0.0,
        }
    return {
        "p_vec": 0.90,
        "p_bm25": 0.80,
        "s_vec": 0.60,
        "p_fts": 0.30,
        "p_keyword": 0.20,
    }


def _tool_index_role(tool_name: str) -> str:
    return "shadow" if _is_shadow_tool(tool_name) else "primary"


def _tool_family(tool_name: str) -> str:
    name = str(tool_name or "").strip().lower()
    if name.endswith("_vec"):
        return "vector"
    if name.endswith("_bm25"):
        return "bm25"
    if name.endswith("_fts"):
        return "fts"
    return "keyword"


def _tool_candidate_k(config: dict[str, Any], tool_name: str) -> int:
    family = _tool_family(tool_name)
    if family == "vector":
        return int(config.get("vector_candidate_k") or config.get("top_k") or 5)
    if family == "bm25":
        return int(config.get("bm25_candidate_k") or config.get("top_k") or 5)
    if family == "fts":
        return int(config.get("fts_candidate_k") or config.get("keyword_candidate_k") or config.get("top_k") or 5)
    return int(config.get("keyword_candidate_k") or config.get("top_k") or 5)


def _lexical_cache_key(
    *,
    tool_name: str,
    query_text: str,
    index_role: str,
    candidate_k: int,
) -> tuple[str, str, str, int] | None:
    family = _tool_family(tool_name)
    if family not in {"bm25", "fts", "keyword"}:
        return None
    normalized_query = " ".join(str(query_text or "").split()).strip()
    if not normalized_query:
        return None
    normalized_role = str(index_role or "").strip().lower() or "primary"
    return (
        str(tool_name or "").strip().lower(),
        normalized_query.lower(),
        normalized_role,
        max(1, int(candidate_k or 1)),
    )


def _lookup_lexical_cache(
    lexical_result_cache: dict[tuple[str, str, str, int], tuple[str, list[RetrievedChunk]]] | None,
    *,
    cache_key: tuple[str, str, str, int] | None,
) -> tuple[str, list[RetrievedChunk]] | None:
    if lexical_result_cache is None or cache_key is None:
        return None
    cached = lexical_result_cache.get(cache_key)
    if cached is not None:
        return cached
    tool_name, query_text, index_role, candidate_k = cache_key
    fallback_key: tuple[str, str, str, int] | None = None
    fallback_limit: int | None = None
    for existing_key in lexical_result_cache:
        existing_tool, existing_query, existing_role, existing_limit = existing_key
        if existing_tool != tool_name or existing_query != query_text or existing_role != index_role:
            continue
        if existing_limit < candidate_k:
            continue
        if fallback_limit is None or existing_limit < fallback_limit:
            fallback_key = existing_key
            fallback_limit = existing_limit
    if fallback_key is None:
        return None
    cached_tool_label, cached_chunks = lexical_result_cache[fallback_key]
    return cached_tool_label, list(cached_chunks[:candidate_k])


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
    shadow_retrieval_enabled: bool | None = None,
) -> tuple[list[str], list[str]]:
    if shadow_retrieval_enabled is None:
        shadow_retrieval_enabled = _shadow_retrieval_enabled()
    original_message = plan.query_variants[0][1] if plan.query_variants else ""
    if (
        round_index > 1
        and recovery_action == "lexical_recovery"
        and _is_short_lexical_faq_bucket(original_message, plan)
    ):
        return _filter_shadow_tool_names(["p_bm25"], shadow_retrieval_enabled=shadow_retrieval_enabled)
    if plan.query_class == "api_semantics_mismatch":
        if round_index <= 1:
            return _filter_shadow_tool_names(["p_bm25"], shadow_retrieval_enabled=shadow_retrieval_enabled)
        if recovery_action == "lexical_recovery":
            return _filter_shadow_tool_names(["p_bm25"], shadow_retrieval_enabled=shadow_retrieval_enabled)
        return _filter_shadow_tool_names(["p_bm25", "p_fts", "p_vec"], shadow_retrieval_enabled=shadow_retrieval_enabled)
    if plan.query_class == "usage_configuration":
        if round_index <= 1:
            return _filter_shadow_tool_names(["p_bm25", "p_fts"], shadow_retrieval_enabled=shadow_retrieval_enabled)
        if recovery_action == "configuration_recovery":
            return _filter_shadow_tool_names(
                ["p_vec", "s_vec", "p_bm25", "s_bm25", "p_fts", "s_fts"],
                shadow_retrieval_enabled=shadow_retrieval_enabled,
            )
    if plan.light_path:
        if round_index <= 1:
            return _filter_shadow_tool_names(["p_bm25", "p_fts"], shadow_retrieval_enabled=shadow_retrieval_enabled)
        if plan.query_class in {"how_to_faq", "usage_configuration"} and recovery_action == "lexical_recovery":
            return _filter_shadow_tool_names(["p_vec"], shadow_retrieval_enabled=shadow_retrieval_enabled)
        if recovery_action == "lexical_recovery":
            return _filter_shadow_tool_names(["p_bm25", "p_fts"], shadow_retrieval_enabled=shadow_retrieval_enabled)
        return _filter_shadow_tool_names(["p_bm25", "p_fts"], shadow_retrieval_enabled=shadow_retrieval_enabled)
    if round_index <= 1:
        return _filter_shadow_tool_names(plan.first_pass_tools, shadow_retrieval_enabled=shadow_retrieval_enabled)
    if recovery_action == "lexical_recovery":
        return _filter_shadow_tool_names(
            ["p_bm25", "p_fts", "p_vec", "s_bm25"],
            shadow_retrieval_enabled=shadow_retrieval_enabled,
        )
    if recovery_action == "semantic_recovery":
        return _filter_shadow_tool_names(
            ["p_vec", "s_vec", "p_bm25", "s_bm25", "p_fts", "s_fts"],
            shadow_retrieval_enabled=shadow_retrieval_enabled,
        )
    if recovery_action == "compare_recovery":
        return _filter_shadow_tool_names(
            ["p_vec", "p_bm25", "s_vec", "p_fts"],
            shadow_retrieval_enabled=shadow_retrieval_enabled,
        )
    return _filter_shadow_tool_names(plan.first_pass_tools, shadow_retrieval_enabled=shadow_retrieval_enabled)


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
    if plan.query_class == "usage_configuration":
        if round_index <= 1:
            return _dedupe_agentic_variants(variants[:1])
        if recovery_action == "configuration_recovery":
            if _is_generic_join_channel_query(message):
                variants.extend(_generic_join_recovery_variants())
            return _dedupe_agentic_variants(variants)
    short_faq_variants = (
        _short_lexical_faq_recovery_variants(message, plan.exact_terms)
        if (
            round_index > 1
            and recovery_action == "lexical_recovery"
            and _is_short_lexical_faq_bucket(message, plan)
        )
        else []
    )
    if short_faq_variants:
        return short_faq_variants
    if plan.query_class == "api_semantics_mismatch":
        if round_index > 1:
            existing = {query.lower() for _, query in variants}
            anchor_variant = build_anchor_variant(message)
            if anchor_variant and anchor_variant.lower() not in existing:
                variants.append(("anchor", anchor_variant))
        return _dedupe_agentic_variants(variants)
    if plan.light_path:
        if plan.query_class == "how_to_faq":
            return _dedupe_agentic_variants(variants[:1])
        if round_index > 1 and recovery_action == "lexical_recovery":
            existing = {query.lower() for _, query in variants}
            exact_query = " ".join(plan.exact_terms).strip()
            if exact_query and exact_query.lower() not in existing and not _is_generic_join_channel_query(message):
                variants.append(("exact_token", exact_query))
                existing.add(exact_query.lower())
            if _is_generic_join_channel_query(message):
                variants.extend(_generic_join_recovery_variants())
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
            if exact_query and exact_query.lower() not in existing and not _is_generic_join_channel_query(message):
                variants.append(("exact_token", exact_query))
            if _is_generic_join_channel_query(message):
                variants.extend(_generic_join_recovery_variants())
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


def _chunk_query_variant_kinds(chunk: RetrievedChunk) -> list[str]:
    trace = chunk.candidate_trace if isinstance(chunk.candidate_trace, dict) else {}
    variants = trace.get("query_variants")
    if not isinstance(variants, list):
        query_kind = str(trace.get("query_kind") or "").strip()
        return [query_kind] if query_kind else []
    kinds: list[str] = []
    seen: set[str] = set()
    for item in variants:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if not kind or kind in seen:
            continue
        seen.add(kind)
        kinds.append(kind)
    if not kinds:
        query_kind = str(trace.get("query_kind") or "").strip()
        if query_kind:
            kinds.append(query_kind)
    return kinds


def _inject_generic_join_candidates(
    retrieved_chunks: list[RetrievedChunk],
    *,
    tool_results: dict[str, list[RetrievedChunk]],
    product: str | None,
    limit: int,
    accepted_variant_kinds: set[str],
    query: str | None = None,
) -> list[RetrievedChunk]:
    require_preferred_join_step = _generic_join_requires_role_agnostic_guidance(query or "")

    def _is_generic_join_pinned_chunk(chunk: RetrievedChunk) -> bool:
        return any(
            str(source or "").startswith("generic_join_")
            for source in (chunk.retrieval_sources or [])
        )

    def _product_rank(chunk: RetrievedChunk) -> int:
        normalized_product = _normalized_query_text(product)
        chunk_product = _chunk_product(chunk)
        if normalized_product != SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING:
            return 1
        if chunk_product == "video-calling":
            return 3
        if chunk_product == "voice-calling":
            return 2
        if chunk_product in _AUDIO_VIDEO_CALLING_CORE_PRODUCTS:
            return 1
        return 0

    if not retrieved_chunks:
        retrieved_chunks = []
    focused_candidates: list[RetrievedChunk] = []
    seen: set[str] = set()
    for chunk in retrieved_chunks:
        if not _is_generic_join_pinned_chunk(chunk):
            continue
        if not _generic_join_product_compatible(chunk, product):
            continue
        if not (_chunk_covers_generic_join_step(chunk) or _is_token_auth_chunk(chunk)):
            continue
        dedupe_key = _chunk_dedupe_key(chunk)
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        focused_candidates.append(_copy_chunk(chunk))
    for tool_name in ["p_bm25", "p_fts", "p_keyword"]:
        for chunk in tool_results.get(tool_name, []) or []:
            kinds = _chunk_query_variant_kinds(chunk)
            if not any(kind in accepted_variant_kinds for kind in kinds):
                continue
            if not _generic_join_product_compatible(chunk, product):
                continue
            if not (_chunk_covers_generic_join_step(chunk) or _is_token_auth_chunk(chunk)):
                continue
            dedupe_key = _chunk_dedupe_key(chunk)
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            focused_candidates.append(_copy_chunk(chunk))
    if not focused_candidates:
        return list(retrieved_chunks)
    focused_candidates = sorted(
        focused_candidates,
        key=lambda chunk: (
            1 if _is_generic_join_pinned_chunk(chunk) else 0,
            _product_rank(chunk),
            1 if _is_preferred_generic_join_step_chunk(chunk) else 0,
            1 if _is_token_auth_chunk(chunk) else 0,
            _score_from_candidate_trace(chunk),
            float(chunk.similarity or 0.0),
        ),
        reverse=True,
    )
    auth_candidates = [chunk for chunk in focused_candidates if _is_token_auth_chunk(chunk)]
    best_auth = auth_candidates[0] if auth_candidates else None

    preferred_join_candidates = [
        chunk
        for chunk in focused_candidates
        if _is_preferred_generic_join_step_chunk(chunk)
    ]
    any_join_candidates = [
        chunk for chunk in focused_candidates if _is_join_channel_step_chunk(chunk)
    ]
    join_candidates = preferred_join_candidates or ([] if require_preferred_join_step else any_join_candidates)
    if best_auth is not None:
        auth_product = _chunk_product(best_auth)
        auth_platform = _chunk_platform(best_auth)
        join_candidates = sorted(
            join_candidates,
            key=lambda chunk: (
                1 if _is_generic_join_pinned_chunk(chunk) else 0,
                1 if _chunk_product(chunk) == auth_product and auth_product else 0,
                1 if _chunk_platform(chunk) == auth_platform and auth_platform else 0,
                1 if _is_preferred_generic_join_step_chunk(chunk) else 0,
                _product_rank(chunk),
                _score_from_candidate_trace(chunk),
                float(chunk.similarity or 0.0),
            ),
            reverse=True,
        )
    best_join = join_candidates[0] if join_candidates else None
    if require_preferred_join_step and best_join is None and best_auth is not None:
        auth_product = _chunk_product(best_auth)
        auth_platform = _chunk_platform(best_auth)
        aligned_role_specific_candidates = [
            chunk
            for chunk in any_join_candidates
            if _chunk_product(chunk) == auth_product
            and _chunk_platform(chunk) == auth_platform
        ]
        if aligned_role_specific_candidates:
            aligned_role_specific_candidates = sorted(
                aligned_role_specific_candidates,
                key=lambda chunk: (
                    1 if _is_generic_join_pinned_chunk(chunk) else 0,
                    _product_rank(chunk),
                    _score_from_candidate_trace(chunk),
                    float(chunk.similarity or 0.0),
                ),
                reverse=True,
            )
            best_join = aligned_role_specific_candidates[0]

    rescue_chunks: list[RetrievedChunk] = []
    for chunk in [best_join, best_auth]:
        if chunk is None:
            continue
        dedupe_key = _chunk_dedupe_key(chunk)
        if dedupe_key and any(_chunk_dedupe_key(existing) == dedupe_key for existing in rescue_chunks):
            continue
        rescue_chunks.append(_copy_chunk(chunk))
    if not rescue_chunks:
        return list(retrieved_chunks)
    if require_preferred_join_step and best_join is None:
        return list(rescue_chunks[: max(1, int(limit or 1))])
    merged: list[RetrievedChunk] = []
    seen = set()
    for chunk in ([_copy_chunk(item) for item in rescue_chunks] + [_copy_chunk(item) for item in retrieved_chunks]):
        dedupe_key = _chunk_dedupe_key(chunk)
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        merged.append(chunk)
        if len(merged) >= max(1, int(limit or 1)):
            break
    return merged


def _inject_generic_join_recovery_candidates(
    retrieved_chunks: list[RetrievedChunk],
    *,
    tool_results: dict[str, list[RetrievedChunk]],
    product: str | None,
    limit: int,
    query: str | None = None,
) -> list[RetrievedChunk]:
    return _inject_generic_join_candidates(
        retrieved_chunks,
        tool_results=tool_results,
        product=product,
        limit=limit,
        accepted_variant_kinds=set(_GENERIC_JOIN_FOCUSED_VARIANT_KINDS),
        query=query,
    )


def _inject_generic_join_original_candidates(
    retrieved_chunks: list[RetrievedChunk],
    *,
    tool_results: dict[str, list[RetrievedChunk]],
    product: str | None,
    limit: int,
    query: str | None = None,
) -> list[RetrievedChunk]:
    return _inject_generic_join_candidates(
        retrieved_chunks,
        tool_results=tool_results,
        product=product,
        limit=limit,
        accepted_variant_kinds={"original"},
        query=query,
    )


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
    if is_api_semantics_mismatch_message(query):
        return _select_api_semantics_final_chunks(ordered, limit=limit, query=query)
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
    lexical_result_cache: dict[tuple[str, str, str, int], tuple[str, list[RetrievedChunk]]] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> tuple[str, list[RetrievedChunk], float, float, float, float, bool, bool]:
    family = _tool_family(tool_name)
    current_tool_label = tool_name
    vector_latency_ms = 0.0
    bm25_latency_ms = 0.0
    fts_latency_ms = 0.0
    keyword_latency_ms = 0.0
    used_seed_tool = False
    used_cached_tool = False
    candidate_limit = _tool_candidate_k(config, tool_name)
    cache_key = _lexical_cache_key(
        tool_name=tool_name,
        query_text=query_text,
        index_role=index_role,
        candidate_k=candidate_limit,
    )

    stage_name = "vector_embedding" if family == "vector" else f"round_{round_index}_retrieval"
    _raise_if_cancelled(
        stage_name,
        should_cancel=should_cancel,
        record_stage=record_cancel_stage,
    )
    if family == "vector" and not bool(
        config.get("_vector_runtime_available", config.get("vector_enabled", True))
    ):
        return current_tool_label, [], 0.0, 0.0, 0.0, 0.0, False, False

    if seeded_chunks:
        raw_chunks = [_copy_chunk(chunk) for chunk in seeded_chunks]
        used_seed_tool = True
        if cache_key is not None and lexical_result_cache is not None:
            lexical_result_cache[cache_key] = (
                current_tool_label,
                [_copy_chunk(chunk) for chunk in raw_chunks],
            )
    else:
        cached_result = _lookup_lexical_cache(
            lexical_result_cache,
            cache_key=cache_key,
        )
        if cached_result is not None:
            cached_tool_label, cached_chunks = cached_result
            current_tool_label = str(cached_tool_label or tool_name).strip() or tool_name
            raw_chunks = [_copy_chunk(chunk) for chunk in cached_chunks]
            used_cached_tool = True
        else:
            started_at = time.perf_counter()
            try:
                if family == "vector":
                    raw_chunks = _retrieve_chunks(
                        query_text,
                        config,
                        limit=candidate_limit,
                        index_role=index_role,
                    )
                    vector_latency_ms += (time.perf_counter() - started_at) * 1000
                elif family == "bm25":
                    raw_chunks = _retrieve_bm25_chunks(
                        query_text,
                        config,
                        limit=candidate_limit,
                        index_role=index_role,
                    )
                    bm25_latency_ms += (time.perf_counter() - started_at) * 1000
                elif family == "fts":
                    raw_chunks = _retrieve_fts_chunks(
                        query_text,
                        config,
                        limit=candidate_limit,
                        index_role=index_role,
                    )
                    fts_latency_ms += (time.perf_counter() - started_at) * 1000
                else:
                    raw_chunks = _retrieve_keyword_chunks(
                        query_text,
                        config,
                        limit=candidate_limit,
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
                    return current_tool_label, [], vector_latency_ms, bm25_latency_ms, fts_latency_ms, keyword_latency_ms, used_seed_tool, used_cached_tool
                if family not in {"bm25", "fts"}:
                    logger.warning("RAG %s retrieval failed for %s query: %s", tool_name, query_kind, exc)
                    return current_tool_label, [], vector_latency_ms, bm25_latency_ms, fts_latency_ms, keyword_latency_ms, used_seed_tool, used_cached_tool
                logger.warning("RAG %s retrieval failed for %s query, trying keyword fallback: %s", tool_name, query_kind, exc)
                started_at = time.perf_counter()
                try:
                    raw_chunks = _retrieve_keyword_chunks(
                        query_text,
                        config,
                        limit=_tool_candidate_k(config, "p_keyword" if index_role == "primary" else "s_keyword"),
                        index_role=index_role,
                    )
                    keyword_latency_ms += (time.perf_counter() - started_at) * 1000
                    current_tool_label = f"{'s' if index_role == 'shadow' else 'p'}_keyword"
                except Exception as keyword_exc:
                    logger.warning("RAG keyword retrieval failed for %s query: %s", query_kind, keyword_exc)
                    return current_tool_label, [], vector_latency_ms, bm25_latency_ms, fts_latency_ms, keyword_latency_ms, used_seed_tool, used_cached_tool
            if cache_key is not None and lexical_result_cache is not None:
                lexical_result_cache[cache_key] = (
                    current_tool_label,
                    [_copy_chunk(chunk) for chunk in raw_chunks],
                )

    for chunk in raw_chunks:
        chunk.index_role = index_role
        chunk.retrieval_sources = list(dict.fromkeys([*chunk.retrieval_sources, current_tool_label]))
        chunk.candidate_trace["tool_name"] = current_tool_label
        chunk.candidate_trace["query_kind"] = query_kind
        chunk.candidate_trace["query_round"] = round_index
        chunk.candidate_trace["raw_score"] = _score_from_candidate_trace(chunk)
        chunk.candidate_trace["index_role"] = index_role

    return current_tool_label, raw_chunks, vector_latency_ms, bm25_latency_ms, fts_latency_ms, keyword_latency_ms, used_seed_tool, used_cached_tool


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
    lexical_result_cache: dict[tuple[str, str, str, int], tuple[str, list[RetrievedChunk]]] | None = None,
    deadline: RagDeadline | None = None,
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

    tool_names, shadow_tools_skipped = _agentic_round_tools(
        plan,
        round_index=round_index,
        recovery_action=recovery_action,
        shadow_retrieval_enabled=_shadow_retrieval_enabled(config),
    )
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
    fts_latency_ms = 0.0
    keyword_latency_ms = 0.0
    retrieval_tool_timings: list[dict[str, Any]] = []
    variant_config = dict(config)
    if (
        round_index > 1
        and recovery_action == "lexical_recovery"
        and _is_short_lexical_faq_bucket(message, plan)
    ):
        variant_config = _apply_short_lexical_faq_recovery_budget(variant_config)
    variant_config["_retrieval_plan"] = retrieval_plan
    used_seed_tools: list[str] = []
    zero_yield_expansion_counts: dict[str, int] = {
        "vector": 0,
        "bm25": 0,
    }
    troubleshooting_zero_yield_skips: set[str] = set()
    troubleshooting_original_support_weak = False
    short_faq_round = (
        round_index > 1
        and recovery_action == "lexical_recovery"
        and _is_short_lexical_faq_bucket(message, plan)
    )
    short_faq_original_query_text = ""
    short_faq_original_tool_results: dict[str, list[RetrievedChunk]] = {}
    short_faq_original_chunks: list[RetrievedChunk] = []
    if short_faq_round:
        short_faq_original_query_text, short_faq_original_tool_results = _load_short_faq_original_tool_results(
            message=message,
            plan=plan,
            config=variant_config,
            lexical_result_cache=lexical_result_cache,
        )
        short_faq_original_chunks = [
            _copy_chunk(chunk)
            for chunks in short_faq_original_tool_results.values()
            for chunk in chunks
        ]

    def _consume_tool_result(
        *,
        family: str,
        tool_name: str,
        query_kind: str,
        query_text: str,
        index_role: str,
        result: tuple[str, list[RetrievedChunk], float, float, float, float, bool, bool],
    ) -> None:
        nonlocal vector_latency_ms, bm25_latency_ms, fts_latency_ms, keyword_latency_ms
        current_tool_label, raw_chunks, vector_ms, bm25_ms, fts_ms, keyword_ms, used_seed_tool, used_cached_tool = result
        vector_latency_ms += vector_ms
        bm25_latency_ms += bm25_ms
        fts_latency_ms += fts_ms
        keyword_latency_ms += keyword_ms
        if used_seed_tool:
            used_seed_tools.append(tool_name)
        tool_latency_ms = (
            vector_ms
            if family == "vector"
            else bm25_ms
            if family == "bm25"
            else fts_ms
            if family == "fts"
            else keyword_ms
        )
        zero_yield_reason = None
        if not raw_chunks:
            if used_seed_tool:
                zero_yield_reason = "seeded_chunks_empty"
            elif family in {"vector", "bm25", "keyword"} and downpush_hard_filters(
                retrieval_plan,
                query_policy=str(config.get("query_policy") or ""),
            ):
                zero_yield_reason = "no_match_after_downpush_filters"
            elif query_kind in {"semantic", "rewrite"}:
                zero_yield_reason = "variant_no_match"
            else:
                zero_yield_reason = "no_match"
        retrieval_tool_timings.append(
            {
                "tool_name": current_tool_label,
                "query_kind": query_kind,
                "round_index": round_index,
                "index_role": index_role,
                "latency_ms": round(tool_latency_ms, 2),
                "candidate_count": len(raw_chunks),
                "zero_yield_reason": zero_yield_reason,
                "used_seed_tool": used_seed_tool,
                "used_cached_tool": used_cached_tool,
            }
        )
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

    def _empty_tool_result(
        tool_name: str,
    ) -> tuple[str, list[RetrievedChunk], float, float, float, float, bool, bool]:
        return tool_name, [], 0.0, 0.0, 0.0, 0.0, False, False

    def _retrieve_tool_variant_with_deadline(
        *,
        tool_name: str,
        query_kind: str,
        query_text: str,
        index_role: str,
        seeded_chunks: list[RetrievedChunk] | None = None,
    ) -> tuple[str, list[RetrievedChunk], float, float, float, float, bool, bool]:
        stage = f"round_{round_index}_retrieval"
        if deadline is None:
            return _retrieve_agentic_tool_variant(
                tool_name=tool_name,
                query_kind=query_kind,
                query_text=query_text,
                config=variant_config,
                index_role=index_role,
                round_index=round_index,
                seeded_chunks=seeded_chunks,
                lexical_result_cache=lexical_result_cache,
                should_cancel=should_cancel,
                record_cancel_stage=record_cancel_stage,
            )

        timeout_seconds = deadline.remaining_seconds(stage)
        if timeout_seconds <= 0:
            deadline.mark_timeout(stage)
            return _empty_tool_result(tool_name)

        future: Future[tuple[str, list[RetrievedChunk], float, float, float, float, bool, bool]] | None = None
        retrieval_timed_out = False
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(
                _retrieve_agentic_tool_variant,
                tool_name=tool_name,
                query_kind=query_kind,
                query_text=query_text,
                config=variant_config,
                index_role=index_role,
                round_index=round_index,
                seeded_chunks=seeded_chunks,
                lexical_result_cache=lexical_result_cache,
                should_cancel=should_cancel,
                record_cancel_stage=record_cancel_stage,
            )
            result = future.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            retrieval_timed_out = True
            deadline.mark_timeout(stage)
            if future is not None:
                future.cancel()
            return _empty_tool_result(tool_name)
        finally:
            executor.shutdown(wait=not retrieval_timed_out, cancel_futures=retrieval_timed_out)

        if deadline.is_exhausted():
            deadline.mark_timeout(stage)
        return result

    retrieval_started_at = time.perf_counter()
    short_faq_sparse_requests = (
        _short_lexical_faq_recovery_requests(
            message,
            plan.exact_terms,
            original_chunks=short_faq_original_chunks,
            product=plan.product,
        )
        if short_faq_round
        else []
    )
    troubleshooting_stageized_round = (
        plan.query_class == "troubleshooting_why"
        and round_index == 1
        and not short_faq_round
        and not plan.light_path
    )
    effective_tool_results = tool_results
    if short_faq_round:
        for tool_name, query_kind, query_text in short_faq_sparse_requests:
            family = _tool_family(tool_name)
            index_role = _tool_index_role(tool_name)
            result = _retrieve_tool_variant_with_deadline(
                tool_name=tool_name,
                query_kind=query_kind,
                query_text=query_text,
                index_role=index_role,
                seeded_chunks=None,
            )
            _consume_tool_result(
                family=family,
                tool_name=tool_name,
                query_kind=query_kind,
                query_text=query_text,
                index_role=index_role,
                result=result,
            )
        if plan.query_class in {"how_to_faq", "usage_configuration"}:
            effective_tool_results = _merge_tool_result_maps(
                short_faq_original_tool_results,
                tool_results,
            )
            focused_retrieved_chunks = _merge_agentic_tool_results(
                tool_results=effective_tool_results,
                tool_weights=tool_weights,
                limit=int(variant_config.get("fusion_candidate_k") or config.get("fusion_candidate_k") or config.get("top_k") or 5),
                shadow_ratio_cap=float(config.get("agent_shadow_ratio_cap") or 0.4),
            )
            requires_vector_recovery = not focused_retrieved_chunks
            if _is_generic_join_channel_query(message):
                focused_retrieved_chunks = _inject_generic_join_recovery_candidates(
                    focused_retrieved_chunks,
                    tool_results=effective_tool_results,
                    product=plan.product,
                    limit=int(variant_config.get("fusion_candidate_k") or config.get("fusion_candidate_k") or config.get("top_k") or 5),
                    query=message,
                )
                has_join_step, _has_token_auth = _generic_join_support_signals(
                    focused_retrieved_chunks,
                    product=plan.product,
                    query=message,
                )
                top_focus_chunk = focused_retrieved_chunks[0] if focused_retrieved_chunks else None
                requires_vector_recovery = requires_vector_recovery or not has_join_step
                if (
                    top_focus_chunk is not None
                    and (_is_join_multiple_channels_chunk(top_focus_chunk) or _is_stream_channel_chunk(top_focus_chunk))
                ):
                    requires_vector_recovery = True
            if requires_vector_recovery:
                original_query_text = short_faq_original_query_text or _original_query_text_for_plan(message, plan)
                result = _retrieve_tool_variant_with_deadline(
                    tool_name="p_vec",
                    query_kind="original",
                    query_text=original_query_text,
                    index_role="primary",
                    seeded_chunks=None,
                )
                _consume_tool_result(
                    family="vector",
                    tool_name="p_vec",
                    query_kind="original",
                    query_text=original_query_text,
                    index_role="primary",
                    result=result,
                )
                effective_tool_results = _merge_tool_result_maps(
                    short_faq_original_tool_results,
                    tool_results,
                )
    elif troubleshooting_stageized_round:
        original_variants = [item for item in query_variants if item[0] == "original"] or query_variants[:1]
        expansion_variants = [item for item in query_variants if item[0] in {"semantic", "rewrite"}]
        troubleshooting_expansion_hit = False
        short_symptom_troubleshooting = _is_short_symptom_troubleshooting_query(message)

        def _run_troubleshooting_pass(
            variants_to_run: list[tuple[str, str]],
            *,
            allowed_families: set[str] | None = None,
        ) -> None:
            nonlocal troubleshooting_expansion_hit
            for tool_name in tool_names:
                family = _tool_family(tool_name)
                if family == "vector" and not bool(
                    variant_config.get("_vector_runtime_available", variant_config.get("vector_enabled", True))
                ):
                    continue
                if allowed_families is not None and family not in allowed_families:
                    continue
                if family == "fts" and all(query_kind != "original" for query_kind, _ in variants_to_run):
                    continue
                index_role = _tool_index_role(tool_name)
                for query_kind, query_text in variants_to_run:
                    if family == "fts" and query_kind != "original":
                        continue
                    seeded_chunks = None
                    if round_index == 1 and query_kind == "original":
                        seeded_chunks = list((seed_tool_results or {}).get(tool_name) or [])
                    result = _retrieve_tool_variant_with_deadline(
                        tool_name=tool_name,
                        query_kind=query_kind,
                        query_text=query_text,
                        index_role=index_role,
                        seeded_chunks=seeded_chunks,
                    )
                    _consume_tool_result(
                        family=family,
                        tool_name=tool_name,
                        query_kind=query_kind,
                        query_text=query_text,
                        index_role=index_role,
                        result=result,
                    )
                    candidate_count = len(result[1])
                    if family == "fts" and query_kind == "original" and candidate_count == 0:
                        break
                    if family in {"vector", "bm25"} and query_kind in {"semantic", "rewrite"}:
                        if candidate_count == 0:
                            zero_yield_expansion_counts[family] += 1
                        else:
                            troubleshooting_expansion_hit = True
                            zero_yield_expansion_counts[family] = 0
                        if zero_yield_expansion_counts[family] >= 2:
                            break
                    elif family in {"vector", "bm25"} and query_kind == "original":
                        zero_yield_expansion_counts[family] = 0

        def _build_interim_support() -> tuple[list[RetrievedChunk], list[RetrievedChunk], list[RetrievedChunk]]:
            interim_retrieved_chunks = _merge_agentic_tool_results(
                tool_results=tool_results,
                tool_weights=tool_weights,
                limit=int(variant_config.get("fusion_candidate_k") or config.get("fusion_candidate_k") or config.get("top_k") or 5),
                shadow_ratio_cap=float(config.get("agent_shadow_ratio_cap") or 0.4),
            )
            interim_reranked_chunks = list(interim_retrieved_chunks)
            if interim_reranked_chunks:
                interim_reranked_chunks, _ = _metadata_rerank(
                    query=message,
                    chunks=interim_reranked_chunks,
                    top_k=int(variant_config.get("fusion_candidate_k") or config.get("fusion_candidate_k") or config.get("top_k") or 5),
                    retrieval_plan=retrieval_plan,
                    query_understanding=query_understanding,
                    product=plan.product,
                    query_policy=str(config.get("query_policy") or ""),
                )
                interim_reranked_chunks = _reorder_chunks_for_rerank(
                    interim_reranked_chunks or interim_retrieved_chunks,
                    limit=int(variant_config.get("rerank_top_n") or config.get("rerank_top_n") or len(interim_retrieved_chunks) or 1),
                    query=message,
                ) or interim_reranked_chunks or interim_retrieved_chunks
            interim_final_chunks = _select_agentic_final_chunks(
                interim_reranked_chunks,
                limit=int(config.get("top_k") or 5),
                query=message,
                shadow_cap=int(config.get("agent_final_shadow_cap") or 1),
            )
            if sum(1 for chunk in interim_final_chunks if str(chunk.index_role or "").strip().lower() == "primary") == 0:
                interim_final_chunks = _select_agentic_final_chunks(
                    interim_reranked_chunks,
                    limit=int(config.get("top_k") or 5),
                    query=message,
                    shadow_cap=int(config.get("agent_recovery_shadow_cap") or 2),
                )
            return interim_retrieved_chunks, interim_reranked_chunks, interim_final_chunks

        if short_symptom_troubleshooting:
            _run_troubleshooting_pass(original_variants, allowed_families={"bm25", "fts"})
            _interim_retrieved_chunks, interim_reranked_chunks, interim_final_chunks = _build_interim_support()
            if _troubleshooting_should_try_vector_after_lexical_support(
                message=message,
                reranked_chunks=interim_reranked_chunks,
                final_chunks=interim_final_chunks,
            ):
                _run_troubleshooting_pass(original_variants, allowed_families={"vector"})
                _interim_retrieved_chunks, interim_reranked_chunks, interim_final_chunks = _build_interim_support()
            else:
                troubleshooting_original_support_weak = True

            expansion_families = (
                _troubleshooting_expansion_families_after_original(
                    reranked_chunks=interim_reranked_chunks,
                    final_chunks=interim_final_chunks,
                )
                if not troubleshooting_original_support_weak
                else set()
            )
        else:
            _run_troubleshooting_pass(original_variants)
            _interim_retrieved_chunks, interim_reranked_chunks, interim_final_chunks = _build_interim_support()
            expansion_families = _troubleshooting_expansion_families_after_original(
                reranked_chunks=interim_reranked_chunks,
                final_chunks=interim_final_chunks,
            )
        if expansion_families and expansion_variants:
            _run_troubleshooting_pass(expansion_variants, allowed_families=expansion_families)
            if not troubleshooting_expansion_hit:
                troubleshooting_original_support_weak = True
        else:
            troubleshooting_original_support_weak = True
    elif plan.light_path and round_index == 1 and len(query_variants) == 1 and set(tool_names) == {"p_bm25", "p_fts"}:
        query_kind, query_text = query_variants[0]
        executor = ThreadPoolExecutor(max_workers=2)
        tool_future_timed_out = False
        try:
            future_map: dict[
                Future[tuple[str, list[RetrievedChunk], float, float, float, float, bool, bool]],
                tuple[str, str, str],
            ] = {}
            for tool_name in tool_names:
                family = _tool_family(tool_name)
                index_role = _tool_index_role(tool_name)
                future_map[
                    executor.submit(
                        _retrieve_agentic_tool_variant,
                        tool_name=tool_name,
                        query_kind=query_kind,
                        query_text=query_text,
                        config=variant_config,
                        index_role=index_role,
                        round_index=round_index,
                        seeded_chunks=None,
                        lexical_result_cache=lexical_result_cache,
                        should_cancel=should_cancel,
                        record_cancel_stage=record_cancel_stage,
                    )
                ] = (tool_name, family, index_role)
            for future, (tool_name, family, index_role) in future_map.items():
                try:
                    result = (
                        future.result(timeout=deadline.remaining_seconds(f"round_{round_index}_retrieval"))
                        if deadline is not None
                        else future.result()
                    )
                except FutureTimeoutError:
                    if deadline is not None:
                        deadline.mark_timeout(f"round_{round_index}_retrieval")
                    future.cancel()
                    tool_future_timed_out = True
                    continue
                _consume_tool_result(
                    family=family,
                    tool_name=tool_name,
                    query_kind=query_kind,
                    query_text=query_text,
                    index_role=index_role,
                    result=result,
                )
        finally:
            executor.shutdown(wait=not tool_future_timed_out, cancel_futures=tool_future_timed_out)
    else:
        for tool_name in tool_names:
            family = _tool_family(tool_name)
            if family == "vector" and not bool(
                variant_config.get("_vector_runtime_available", variant_config.get("vector_enabled", True))
            ):
                continue
            index_role = _tool_index_role(tool_name)
            for query_kind, query_text in query_variants:
                if plan.query_class == "troubleshooting_why":
                    if family == "fts" and "fts_after_zero_original" in troubleshooting_zero_yield_skips:
                        break
                    if family == "vector" and "vector_after_zero_expansion" in troubleshooting_zero_yield_skips:
                        break
                    if family == "bm25" and "bm25_after_zero_expansion" in troubleshooting_zero_yield_skips:
                        break
                if family == "vector" and not bool(
                    variant_config.get("_vector_runtime_available", variant_config.get("vector_enabled", True))
                ):
                    break
                seeded_chunks = None
                if round_index == 1 and query_kind == "original":
                    seeded_chunks = list((seed_tool_results or {}).get(tool_name) or [])
                result = _retrieve_tool_variant_with_deadline(
                    tool_name=tool_name,
                    query_kind=query_kind,
                    query_text=query_text,
                    index_role=index_role,
                    seeded_chunks=seeded_chunks,
                )
                _consume_tool_result(
                    family=family,
                    tool_name=tool_name,
                    query_kind=query_kind,
                    query_text=query_text,
                    index_role=index_role,
                    result=result,
                )
                if plan.query_class != "troubleshooting_why":
                    continue
                candidate_count = len(result[1])
                if family == "fts" and query_kind == "original" and candidate_count == 0:
                    troubleshooting_zero_yield_skips.add("fts_after_zero_original")
                    break
                if family in {"vector", "bm25"} and query_kind in {"semantic", "rewrite"}:
                    if candidate_count == 0:
                        zero_yield_expansion_counts[family] += 1
                    else:
                        zero_yield_expansion_counts[family] = 0
                    if zero_yield_expansion_counts[family] >= 2:
                        troubleshooting_zero_yield_skips.add(f"{family}_after_zero_expansion")
                        break
                elif family in {"vector", "bm25"} and query_kind not in {"semantic", "rewrite"}:
                    zero_yield_expansion_counts[family] = 0
    retrieval_wall_clock_ms = round((time.perf_counter() - retrieval_started_at) * 1000, 2)

    fusion_limit = int(variant_config.get("fusion_candidate_k") or config.get("fusion_candidate_k") or config.get("top_k") or 5)
    retrieved_chunks = _merge_agentic_tool_results(
        tool_results=effective_tool_results,
        tool_weights=tool_weights,
        limit=fusion_limit,
        shadow_ratio_cap=float(config.get("agent_shadow_ratio_cap") or 0.4),
    )
    if round_index == 1 and _is_generic_join_channel_query(message):
        pinned_chunks = _fetch_generic_join_pinned_chunks(
            message=message,
            config=variant_config,
            existing_chunks=retrieved_chunks,
            product=plan.product,
            index_role="primary",
        )
        if pinned_chunks:
            retrieved_chunks = _prepend_generic_join_pinned_chunks(
                message=message,
                chunks=retrieved_chunks,
                pinned_chunks=pinned_chunks,
            )
    if round_index == 1 and _is_generic_join_channel_query(message):
        retrieved_chunks = _inject_generic_join_original_candidates(
            retrieved_chunks,
            tool_results=tool_results,
            product=plan.product,
            limit=fusion_limit,
            query=message,
        )
    if round_index > 1 and recovery_action == "lexical_recovery" and _is_generic_join_channel_query(message):
        retrieved_chunks = _inject_generic_join_recovery_candidates(
            retrieved_chunks,
            tool_results=effective_tool_results,
            product=plan.product,
            limit=fusion_limit,
            query=message,
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
            top_k=fusion_limit,
            retrieval_plan=retrieval_plan,
            query_understanding=query_understanding,
            product=plan.product,
            query_policy=str(config.get("query_policy") or ""),
        )
        rerank_latency_ms += (time.perf_counter() - rerank_started_at) * 1000
        reranked_chunks = _reorder_chunks_for_rerank(
            reranked_chunks or retrieved_chunks,
            limit=int(variant_config.get("rerank_top_n") or config.get("rerank_top_n") or len(retrieved_chunks) or 1),
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

    if plan.query_class == "api_semantics_mismatch":
        pinned_chunks = _fetch_api_semantics_pinned_chunks(
            message=message,
            config=config,
            existing_chunks=reranked_chunks,
            index_role="primary",
        )
        if pinned_chunks:
            reranked_chunks = _prepend_api_semantics_pinned_chunks(
                message=message,
                chunks=reranked_chunks,
                pinned_chunks=pinned_chunks,
            )

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
    if _is_generic_join_channel_query(message):
        generic_join_support_pool = list(reranked_chunks)
        for tool_chunks in effective_tool_results.values():
            generic_join_support_pool.extend(_copy_chunk(chunk) for chunk in tool_chunks or [])
        final_chunks = _enforce_generic_join_support_pair(
            final_chunks,
            reranked_chunks=generic_join_support_pool,
            product=plan.product,
            query=message,
            limit=int(config.get("top_k") or 5),
        )

    grounded_overlap = _has_grounded_keyword_overlap(message, final_chunks)
    troubleshooting_recovery_unlikely = _troubleshooting_recovery_unlikely_from_timings(
        query_class=plan.query_class,
        round_index=round_index,
        retrieval_tool_timings=retrieval_tool_timings,
    )
    if plan.query_class == "troubleshooting_why" and round_index == 1 and troubleshooting_original_support_weak:
        troubleshooting_recovery_unlikely = True
    judge = _judge_agentic_round(
        message=message,
        query_class=plan.query_class,
        round_index=round_index,
        reranked_chunks=reranked_chunks,
        final_chunks=final_chunks,
        decomposition_targets=plan.decomposition_targets,
        exact_terms=plan.exact_terms,
        grounded_overlap=grounded_overlap,
        product=plan.product,
        troubleshooting_recovery_unlikely=troubleshooting_recovery_unlikely,
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
            shadow_tools_skipped=list(shadow_tools_skipped),
        ),
        vector_candidate_count=len(vector_candidate_ids),
        bm25_candidate_count=len(bm25_candidate_ids),
        vector_latency_ms=round(vector_latency_ms, 2),
        bm25_latency_ms=round(bm25_latency_ms, 2),
        fts_latency_ms=round(fts_latency_ms, 2),
        keyword_latency_ms=round(keyword_latency_ms, 2),
        retrieval_wall_clock_ms=retrieval_wall_clock_ms,
        retrieval_tool_timings=list(retrieval_tool_timings),
        rerank_latency_ms=round(rerank_latency_ms, 2),
        used_seed_tools=list(dict.fromkeys(used_seed_tools)),
        shadow_tools_skipped=list(shadow_tools_skipped),
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


def _fanout_enabled() -> bool:
    return _feature_flag_enabled("RAG_MULTI_QUESTION_FANOUT_ENABLED", True)


def _api_semantics_total_deadline_seconds() -> float:
    return _safe_float_env("RAG_API_SEMANTICS_TOTAL_DEADLINE_SECONDS", 20.0)


def _api_semantics_retrieval_deadline_seconds() -> float:
    return _safe_float_env("RAG_API_SEMANTICS_RETRIEVAL_DEADLINE_SECONDS", 8.0)


def _api_semantics_generation_deadline_seconds() -> float:
    return _safe_float_env("RAG_API_SEMANTICS_GENERATION_DEADLINE_SECONDS", 12.0)


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


def _normalize_query_policy(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized == "client_accuracy_first" else ""


def _is_official_guidance_chunk(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    source_path_lower = str(chunk.source_path or "").strip().lower()
    source_url_lower = str(chunk.source_url or "").strip().lower()
    source_family = str(metadata.get("source_family") or "").strip().lower()
    chunk_type = str(metadata.get("chunk_type") or "").strip().lower()
    use_case = str(metadata.get("use_case") or "").strip().lower()
    section_path = " > ".join(_chunk_metadata_list(metadata.get("section_path"))).lower()
    if source_path_lower.startswith("official/") or "docs.agora.io" in source_url_lower:
        return True
    if any(
        marker in source_family
        for marker in [
            "get-started",
            "quickstart",
            "authentication-workflow",
            "api-reference",
            "how-to",
        ]
    ):
        return True
    if chunk_type in {"howto", "api_params", "code", "faq_index"} and any(
        marker in section_path for marker in ["join a channel", "quickstart", "get started", "authentication"]
    ):
        return True
    return use_case in {"join_channel", "basic_authentication"}


def _doc_family_bucket(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    if _is_official_guidance_chunk(chunk):
        return "official_guidance"
    if _is_technical_case_chunk(metadata):
        return "technical_case"
    source_family = str(metadata.get("source_family") or "").strip().lower()
    if source_family:
        return source_family
    return "other"


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
        query_class=_classify_agentic_query(text, None),
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
    product: str | None = None,
    query_class: str | None = None,
    query_policy: str | None = None,
) -> tuple[list[RetrievedChunk], dict[str, Any]]:
    resolved_hints = hints or _extract_metadata_hints(query)
    resolved_query_class = str(query_class or getattr(resolved_hints, "query_class", "") or "")
    resolved_query_policy = _normalize_query_policy(query_policy)
    query_lower = str(query or "").lower()
    api_semantics_query = is_api_semantics_mismatch_message(query)
    answer_first_guidance_query = (
        resolved_query_class in {"how_to_faq", "configuration", "usage_configuration"}
        and is_answer_first_how_to_message(query)
    )
    anchor_hits = {item.lower() for item in extract_anchor_hits(query)}
    endpoint_operation_hints = {item.lower() for item in extract_endpoint_operation_hints(query)}
    api_semantics_parameter_query = bool(
        {"uid", "str_uid", "time", "time_in_seconds", "cname", "ip"} & anchor_hits
    )
    api_semantics_kicking_rule_query = "kicking-rule" in anchor_hits
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
        chunk_product = _normalize_metadata_filter_value("product", metadata.get("product")) or ""
        technical_terms = {item.lower() for item in resolved_hints.technical_terms}
        text_lower = chunk.text.lower()
        source_url_lower = str(chunk.source_url or "").strip().lower()
        source_path_lower = str(chunk.source_path or "").strip().lower()

        if answer_first_guidance_query and _is_official_guidance_chunk(chunk):
            official_boost = 2.1 if resolved_query_policy == "client_accuracy_first" else 1.6
            boost += official_boost
            reasons.append("intent:official_guidance_priority")
            if use_case in {"join_channel", "basic_authentication"}:
                boost += 0.9
                reasons.append(f"intent:use_case:{use_case}")
        if answer_first_guidance_query and _is_technical_case_chunk(metadata):
            technical_penalty = 1.8 if resolved_query_policy == "client_accuracy_first" else 1.3
            boost -= technical_penalty
            reasons.append("intent:technical_case_penalty")

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
        if anchor_hits:
            matched_anchor_hits = [
                hit
                for hit in anchor_hits
                if hit and (
                    hit in source_url_lower
                    or hit in source_path_lower
                    or hit in section_path
                    or hit in text_lower
                )
            ]
            if matched_anchor_hits:
                boost += min(2.4, 1.2 + 0.45 * (len(matched_anchor_hits) - 1))
                reasons.append(f"anchor_hits:{','.join(sorted(set(matched_anchor_hits)))}")
        if api_semantics_query and "disband" in query_lower:
            if "disband a channel" in section_path or "disband-a-channel" in source_url_lower:
                boost += 1.5
                reasons.append("api_semantics:disband_section")
            elif "kick a user out of a channel" in section_path or "kick-a-user-out-of-a-channel" in source_url_lower:
                boost -= 0.9
                reasons.append("api_semantics:wrong_kick_section")
        if api_semantics_query and api_semantics_kicking_rule_query:
            if "create-rules" in source_url_lower or "create-rules" in source_path_lower:
                boost += 1.4
                reasons.append("api_semantics:create_rules_endpoint")
            elif any(token in source_url_lower or token in source_path_lower for token in ("delete-rules", "get-rule-list")):
                boost -= 0.8
                reasons.append("api_semantics:wrong_endpoint_reference")
            if api_semantics_parameter_query and (
                "request parameters" in section_path or chunk_type == "api_params"
            ):
                boost += 1.35
                reasons.append("api_semantics:request_parameters")
        if api_semantics_query and "broadcast-streaming" in anchor_hits and chunk_product == "broadcast-streaming":
            boost += 0.9
            reasons.append("api_semantics:docs_product_match")
        if api_semantics_query and endpoint_operation_hints:
            matched_operation_hints = [
                hint
                for hint in endpoint_operation_hints
                if hint and (
                    hint in source_url_lower
                    or hint in source_path_lower
                    or hint in section_path
                    or hint in text_lower
                )
            ]
            if matched_operation_hints:
                boost += min(1.6, 0.8 + 0.4 * (len(matched_operation_hints) - 1))
                reasons.append(f"api_semantics:endpoint_operation:{','.join(sorted(set(matched_operation_hints)))}")

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
        if applied_hard_filters.get("product") and chunk_product == applied_hard_filters["product"]:
            boost += 0.5
            reasons.append(f"plan_product:{applied_hard_filters['product']}")

        affinity_boost, affinity_reasons = _product_affinity_adjustment(chunk, product)
        if affinity_boost:
            boost += affinity_boost
            reasons.extend(affinity_reasons)
        join_boost, join_reasons = _join_intent_adjustment(query, chunk, product)
        if join_boost:
            boost += join_boost
            reasons.extend(join_reasons)
        if (
            resolved_query_class == "troubleshooting_why"
            and _is_release_note_chunk(chunk)
            and not _is_release_note_lookup_query(query)
        ):
            boost -= 1.35
            reasons.append("intent:troubleshooting_release_note_penalty")

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
            "query_policy": resolved_query_policy or None,
            "cache_hit": bool(retrieval_plan.cache_hit) if retrieval_plan is not None else False,
            "prf_used": bool(retrieval_plan.prf_used) if retrieval_plan is not None else False,
        },
    }


def _variant_candidate_counts_from_timings(retrieval_tool_timings: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for timing in retrieval_tool_timings:
        if not isinstance(timing, dict):
            continue
        query_kind = str(timing.get("query_kind") or "").strip() or "unknown"
        tool_name = str(timing.get("tool_name") or "").strip() or "unknown"
        counts.setdefault(query_kind, {})[tool_name] = int(timing.get("candidate_count") or 0)
    return counts


def _variant_zero_yield_reasons_from_timings(retrieval_tool_timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    for timing in retrieval_tool_timings:
        if not isinstance(timing, dict) or int(timing.get("candidate_count") or 0) > 0:
            continue
        reason = str(timing.get("zero_yield_reason") or "").strip() or "no_match"
        reasons.append(
            {
                "tool_name": str(timing.get("tool_name") or "").strip() or None,
                "query_kind": str(timing.get("query_kind") or "").strip() or None,
                "round_index": int(timing.get("round_index") or 0),
                "reason": reason,
            }
        )
    return reasons


def _doc_family_mix_for_chunks(chunks: list[RetrievedChunk]) -> dict[str, int]:
    mix: dict[str, int] = {}
    for chunk in chunks:
        bucket = _doc_family_bucket(chunk)
        mix[bucket] = mix.get(bucket, 0) + 1
    return mix


def _rag_config_llm_available(config: dict[str, Any]) -> bool:
    if "llm_available" in config:
        return bool(config.get("llm_available"))
    return bool(str(config.get("api_key") or "").strip())


def _get_rag_config(top_k: int | None = None, *, query_policy: str | None = None) -> dict[str, Any]:
    dsn = (os.getenv("PGVECTOR_DSN") or "").strip()
    answer_profile = resolve_model_profile(RAG_ANSWER_SCENARIO)
    compression_profile = resolve_model_profile(RAG_CONTEXT_COMPRESSION_SCENARIO)
    resolved_query_policy = _normalize_query_policy(query_policy)
    final_top_k = max(1, int(top_k)) if top_k is not None else _safe_int_env("RAG_TOP_K", 6)
    vector_candidate_k = _safe_int_env("RAG_VECTOR_CANDIDATE_K", max(40, final_top_k * 10))
    bm25_candidate_k = _safe_int_env("RAG_BM25_CANDIDATE_K", max(40, final_top_k * 10))
    fusion_candidate_k = _safe_int_env("RAG_FUSION_CANDIDATE_K", max(30, final_top_k * 8))
    rerank_top_n = _safe_int_env("RAG_RERANK_TOP_N", max(20, final_top_k * 4))
    shadow_retrieval_enabled = _feature_flag_enabled("RAG_SHADOW_RETRIEVAL_ENABLED", True)
    if resolved_query_policy == "client_accuracy_first":
        final_top_k = max(final_top_k, 8)
        vector_candidate_k = max(vector_candidate_k, 120)
        bm25_candidate_k = max(bm25_candidate_k, 120)
        fusion_candidate_k = max(fusion_candidate_k, 96)
        rerank_top_n = max(rerank_top_n, 48)
        shadow_retrieval_enabled = True
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
        "llm_available": profile_has_invocation_credentials(answer_profile),
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
        "shadow_retrieval_enabled": shadow_retrieval_enabled,
        "rerank_timeout_seconds": _safe_float_env("RAG_RERANK_TIMEOUT_SECONDS", 10.0),
        "rerank_max_retries": _safe_int_env("RAG_RERANK_MAX_RETRIES", 1),
        "request_timeout_seconds": answer_profile.timeout_seconds,
        "max_retries": answer_profile.max_retries,
        "query_policy": resolved_query_policy or None,
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


def _apply_api_semantics_latency_budget(config: dict[str, Any]) -> dict[str, Any]:
    adjusted = dict(config)
    adjusted["bm25_candidate_k"] = min(
        max(1, int(adjusted.get("bm25_candidate_k") or _API_SEMANTICS_BM25_CANDIDATE_K)),
        _API_SEMANTICS_BM25_CANDIDATE_K,
    )
    adjusted["fts_candidate_k"] = 0
    adjusted["fusion_candidate_k"] = min(
        max(1, int(adjusted.get("fusion_candidate_k") or _API_SEMANTICS_RERANK_TOP_N)),
        _API_SEMANTICS_RERANK_TOP_N,
    )
    adjusted["rerank_top_n"] = min(
        max(1, int(adjusted.get("rerank_top_n") or _API_SEMANTICS_RERANK_TOP_N)),
        _API_SEMANTICS_RERANK_TOP_N,
    )
    adjusted["light_path_generation_chunk_limit"] = _API_SEMANTICS_CONTEXT_CHUNK_LIMIT
    return adjusted


def _apply_short_lexical_faq_recovery_budget(config: dict[str, Any]) -> dict[str, Any]:
    adjusted = dict(config)
    adjusted["bm25_candidate_k"] = _SHORT_FAQ_RECOVERY_BM25_CANDIDATE_K
    adjusted["fts_candidate_k"] = _SHORT_FAQ_RECOVERY_FTS_CANDIDATE_K
    adjusted["fusion_candidate_k"] = _SHORT_FAQ_RECOVERY_FUSION_CANDIDATE_K
    adjusted["rerank_top_n"] = _SHORT_FAQ_RECOVERY_RERANK_TOP_N
    adjusted["short_faq_generation_chunk_limit"] = _SHORT_FAQ_CONTEXT_CHUNK_LIMIT
    return adjusted


def _generation_chunk_limit_for_agentic_query(
    *,
    message: str,
    plan: AgenticRetrievalPlan,
    config: dict[str, Any],
) -> int | None:
    if _is_short_lexical_faq_bucket(message, plan):
        return _SHORT_FAQ_CONTEXT_CHUNK_LIMIT
    if plan.query_class == "api_semantics_mismatch":
        return _API_SEMANTICS_CONTEXT_CHUNK_LIMIT
    if plan.light_path:
        return int(config.get("light_path_generation_chunk_limit") or _LIGHT_PATH_CONTEXT_CHUNK_LIMIT)
    return None


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


def probe_customer_rag_index_readiness(top_k: int | None = None) -> RagKnowledgeIndexReadiness:
    config = _get_rag_config(top_k=top_k)
    configured_table = str(config.get("table") or "").strip() or None
    configured_dsn = str(config.get("dsn") or "").strip()
    if not configured_table or not configured_dsn or not bool(config.get("vector_enabled", True)):
        return RagKnowledgeIndexReadiness(
            status="unconfigured",
            configured_table=configured_table,
            resolved_table=configured_table,
            configured_primary_rows=None,
        )

    try:
        configured_primary_rows = _count_primary_rows_in_table(configured_dsn, configured_table)
    except Exception as exc:
        logger.warning(
            "RAG customer index readiness probe failed for %s: %s",
            configured_table,
            exc,
        )
        return RagKnowledgeIndexReadiness(
            status="probe_failed",
            configured_table=configured_table,
            resolved_table=configured_table,
            configured_primary_rows=None,
        )

    if configured_primary_rows is not None and configured_primary_rows > 0:
        return RagKnowledgeIndexReadiness(
            status="ready",
            configured_table=configured_table,
            resolved_table=configured_table,
            configured_primary_rows=configured_primary_rows,
        )

    try:
        resolved_table = _resolve_active_vector_table(dict(config)) or configured_table
    except Exception as exc:
        logger.warning(
            "RAG customer index readiness resolve failed for %s: %s",
            configured_table,
            exc,
        )
        return RagKnowledgeIndexReadiness(
            status="probe_failed",
            configured_table=configured_table,
            resolved_table=configured_table,
            configured_primary_rows=configured_primary_rows,
        )

    status = "configured_table_empty"
    if resolved_table != configured_table:
        status = "fallback_table_selected"
    return RagKnowledgeIndexReadiness(
        status=status,
        configured_table=configured_table,
        resolved_table=resolved_table,
        configured_primary_rows=configured_primary_rows,
    )

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
    downpush_filters = (
        downpush_hard_filters(retrieval_plan, query_policy=str(config.get("query_policy") or ""))
        if retrieval_plan is not None
        else {}
    )
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
    downpush_filters = (
        downpush_hard_filters(retrieval_plan, query_policy=str(config.get("query_policy") or ""))
        if retrieval_plan is not None
        else {}
    )
    filter_sql, filter_params = _metadata_filter_clauses(sql, downpush_filters, metadata_ref="v.metadata")
    app_schema = str(config.get("app_schema") or "supportportal").strip() or "supportportal"
    normalized_index_role = str(index_role or "").strip().lower() or "primary"
    candidate_limit = int(limit or config["bm25_candidate_k"])
    prejoin_limit = max(candidate_limit * 8, 64)
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
        ),
        top_scored AS MATERIALIZED (
            SELECT
                scored.chunk_id,
                scored.bm25_score
            FROM scored
            ORDER BY scored.bm25_score DESC
            LIMIT %s
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
            top_scored.bm25_score
        FROM top_scored
        JOIN {} AS v
          ON v.id = top_scored.chunk_id
        WHERE v.index_role = %s
          {}
        ORDER BY top_scored.bm25_score DESC, v.updated_at DESC
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
                    prejoin_limit,
                    normalized_index_role,
                    *filter_params,
                    candidate_limit,
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
    downpush_filters = (
        downpush_hard_filters(retrieval_plan, query_policy=str(config.get("query_policy") or ""))
        if retrieval_plan is not None
        else {}
    )
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
    supplement_blocks: list[str] = []
    missing_evidence: list[str] = []
    for chunk in chunks:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        evidence_type = str(metadata.get("request_body_evidence_type") or "").strip()
        if evidence_type:
            matched_fields = [
                str(item).strip()
                for item in (metadata.get("request_body_matched_fields") if isinstance(metadata.get("request_body_matched_fields"), list) else [])
                if str(item).strip()
            ]
            supplement_blocks.append(
                f"[{chunk.chunk_id}] evidence_type={evidence_type} matched_fields={','.join(matched_fields) or 'none'}\n"
                f"{chunk.source_path} | {_build_heading(chunk)}\n"
                f"{chunk.text.strip()}"
            )
            for item in (metadata.get("request_body_missing_evidence") if isinstance(metadata.get("request_body_missing_evidence"), list) else []):
                normalized = str(item or "").strip()
                if normalized and normalized not in missing_evidence:
                    missing_evidence.append(normalized)
        blocks.append(
            f"[{chunk.chunk_id}] {chunk.source_path} | {_build_heading(chunk)}\n"
            f"{chunk.text.strip()}"
        )
    context = "\n\n---\n\n".join(blocks)
    if supplement_blocks:
        supplement = "## Request Body Evidence Supplement\n" + "\n\n---\n\n".join(supplement_blocks)
        if missing_evidence:
            supplement += "\n\nMissing evidence:\n" + "\n".join(f"- {item}" for item in missing_evidence)
        context = f"{context}\n\n---\n\n{supplement}" if context else supplement
    return context


def _request_body_evidence_result_for_query(
    question: str,
    config: dict[str, Any],
) -> RequestBodyEvidenceResult | None:
    request_body_query = detect_request_body_evidence_query(question, use_llm=True)
    if not request_body_query.is_request_body_or_api_config:
        return None

    def _retrieve_schema_chunks(search_query: str, evidence_type: str) -> list[RetrievedChunk]:
        _ = evidence_type
        retrieved: list[RetrievedChunk] = []
        for retrieve_fn in (_retrieve_bm25_chunks, _retrieve_fts_chunks, _retrieve_keyword_chunks):
            try:
                retrieved.extend(
                    retrieve_fn(
                        search_query,
                        config,
                        limit=min(8, max(3, int(config.get("top_k") or 5))),
                        index_role="primary",
                    )
                )
            except Exception as exc:
                logger.debug("Request body evidence retrieval failed query=%s error=%s", search_query, exc)
        merged: list[RetrievedChunk] = []
        seen: set[str] = set()
        for chunk in sorted(retrieved, key=lambda item: float(item.similarity or 0.0), reverse=True):
            dedupe_key = chunk.chunk_id or f"{chunk.source_path}:{chunk.text[:120]}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(chunk)
            if len(merged) >= 5:
                break
        return merged

    return run_request_body_evidence_skill(
        request_body_query,
        retrieve_chunks=_retrieve_schema_chunks,
        max_workers=5,
        max_chunks=5,
    )


def _merge_request_body_evidence_into_final_chunks(
    final_chunks: list[RetrievedChunk],
    *,
    request_body_evidence: RequestBodyEvidenceResult | None,
    max_chunks: int,
    retrieved_chunks: list[RetrievedChunk] | None = None,
) -> list[RetrievedChunk]:
    if request_body_evidence is None or not request_body_evidence.triggered or not request_body_evidence.chunks:
        return final_chunks
    for evidence_chunk in request_body_evidence.chunks:
        chunk = evidence_chunk.original_chunk
        if isinstance(chunk, RetrievedChunk):
            chunk.metadata["request_body_evidence_type"] = evidence_chunk.evidence_type
            chunk.metadata["request_body_matched_fields"] = list(evidence_chunk.matched_fields)
            chunk.metadata["request_body_skill_triggered"] = True
            chunk.metadata["request_body_missing_evidence"] = list(request_body_evidence.missing_evidence)
    primary_chunks = list(final_chunks)
    selected_ids = {chunk.chunk_id for chunk in primary_chunks if chunk.chunk_id}
    for chunk in retrieved_chunks or []:
        if not isinstance(chunk, RetrievedChunk):
            continue
        if chunk.chunk_id and chunk.chunk_id in selected_ids:
            continue
        if not is_high_value_troubleshooting_context(chunk):
            continue
        primary_chunks.append(chunk)
        if chunk.chunk_id:
            selected_ids.add(chunk.chunk_id)
        break
    merged = merge_request_body_evidence_chunks(
        primary_chunks=primary_chunks,
        supplement_chunks=list(request_body_evidence.chunks),
        max_chunks=max_chunks,
    )
    return [chunk for chunk in merged if isinstance(chunk, RetrievedChunk)]


def _request_body_trace_values(request_body_evidence: RequestBodyEvidenceResult | None) -> dict[str, Any]:
    if request_body_evidence is None:
        return {
            "request_body_skill_triggered": False,
            "request_body_keys": [],
            "request_body_nested_paths": [],
            "request_body_endpoint_hints": [],
            "request_body_missing_evidence": [],
            "request_body_evidence_chunk_ids": [],
        }
    return {
        "request_body_skill_triggered": bool(request_body_evidence.triggered),
        "request_body_keys": list(request_body_evidence.query.body_keys),
        "request_body_nested_paths": list(request_body_evidence.query.nested_paths),
        "request_body_endpoint_hints": list(request_body_evidence.query.endpoint_hints),
        "request_body_missing_evidence": list(request_body_evidence.missing_evidence),
        "request_body_evidence_chunk_ids": [chunk.chunk_id for chunk in request_body_evidence.chunks if chunk.chunk_id],
    }


def _chunk_map_by_id(chunks: list[RetrievedChunk]) -> dict[str, RetrievedChunk]:
    return {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}


def _build_answer_prompt(question: str, context_block: str) -> str:
    return _build_answer_prompt_for_mode(question, context_block, repair_mode=False, citation_retry=False)


def _build_answer_prompt_for_mode(
    question: str,
    context_block: str,
    *,
    repair_mode: bool,
    citation_retry: bool = False,
) -> str:
    return build_rag_answer_user_prompt(
        question=question,
        context_block=context_block,
        insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
        repair_mode=repair_mode,
        citation_retry_mode=citation_retry,
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


def _parse_standalone_json_block(lines: list[str], start_index: int) -> tuple[Any | None, int]:
    block_lines: list[str] = []
    max_end = min(len(lines), start_index + 160)
    for index in range(start_index, max_end):
        block_lines.append(lines[index])
        candidate = "\n".join(block_lines).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, (dict, list)):
            return parsed, index
    return None, start_index


def _fence_standalone_json_blocks(value: str) -> str:
    lines = str(value or "").splitlines()
    if not lines:
        return str(value or "")
    rendered: list[str] = []
    index = 0
    inside_fence = False
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            inside_fence = not inside_fence
            rendered.append(line)
            index += 1
            continue
        if not inside_fence and stripped.startswith(("{", "[")):
            parsed, end_index = _parse_standalone_json_block(lines, index)
            if parsed is not None:
                rendered.append("```json")
                rendered.extend(json.dumps(parsed, ensure_ascii=False, indent=2).splitlines())
                rendered.append("```")
                index = end_index + 1
                continue
        rendered.append(line)
        index += 1
    return "\n".join(rendered)


def _compose_grounded_answer_email(
    *,
    question: str,
    body: str,
    steps: list[str] | None = None,
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    cleaned_steps = [step.strip() for step in list(steps or []) if isinstance(step, str) and step.strip()]
    return compose_customer_reply_email(
        reply_kind="grounded_answer",
        body=_fence_standalone_json_blocks(body.strip()),
        requester=requester,
        customer_id=customer_id,
        language=detect_customer_reply_language(question, body, *cleaned_steps),
        steps=cleaned_steps,
    )


def _build_answer_text(
    answer: str,
    key_steps: list[str],
    *,
    question: str,
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    cleaned_steps = [step.strip() for step in key_steps if isinstance(step, str) and step.strip()]
    return _compose_grounded_answer_email(
        question=question,
        body=answer.strip(),
        steps=cleaned_steps,
        requester=requester,
        customer_id=customer_id,
    )


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


def _normalize_api_semantics_text(value: str) -> str:
    return " ".join(str(value or "").lower().replace("`", "").split())


def _api_semantics_uid_rule_chunk(chunks: list[RetrievedChunk]) -> RetrievedChunk | None:
    for chunk in chunks:
        if not _is_api_semantics_request_parameters_chunk(chunk, required_terms={"uid", "str_uid"}):
            continue
        text = _normalize_api_semantics_text(_chunk_search_text(chunk))
        if "uid" in text and "do not set it as 0" in text:
            return chunk
    return None


def _api_semantics_time_rule_chunk(chunks: list[RetrievedChunk]) -> RetrievedChunk | None:
    for chunk in chunks:
        if not _is_api_semantics_request_parameters_chunk(chunk, required_terms={"time", "time_in_seconds"}):
            continue
        text = _normalize_api_semantics_text(_chunk_search_text(chunk))
        if "does not take effect" not in text:
            continue
        if "offline" in text or "log in again" in text or "rejoin the channel" in text:
            return chunk
    return None


def _build_api_semantics_grounded_answer(
    message: str,
    chunks: list[RetrievedChunk],
    *,
    requester: str | None = None,
    customer_id: str | None = None,
) -> RagAnswer | None:
    parameter_groups = _api_semantics_parameter_groups(message)
    if not chunks or not parameter_groups:
        return None

    disband_chunk = next((chunk for chunk in chunks if _is_api_semantics_disband_chunk(chunk)), None)
    uid_rule_chunk = _api_semantics_uid_rule_chunk(chunks)
    time_rule_chunk = _api_semantics_time_rule_chunk(chunks)
    sections: list[str] = []
    cited_chunks: list[RetrievedChunk] = []

    for parameter_group in parameter_groups:
        if {"uid", "str_uid"} & parameter_group:
            if disband_chunk is None or uid_rule_chunk is None:
                return None
            sections.append(
                "For disbanding a channel, the docs say to fill in `cname` and leave `uid` and `ip` blank. "
                "The create-rule request parameters also say do not set `uid` to `0`, so you should omit `uid` "
                "instead of sending `uid: 0`."
            )
            cited_chunks.extend([disband_chunk, uid_rule_chunk])
        elif {"time", "time_in_seconds"} & parameter_group:
            if time_rule_chunk is None:
                return None
            sections.append(
                "For `time` or `time_in_seconds`, a value of `0` does not create a persistent rule. "
                "The request parameters say the banning rule does not take effect; instead, the server sets matching "
                "users offline and they can log in again. Use a positive duration if you need a stored rule."
            )
            cited_chunks.append(time_rule_chunk)
        else:
            return None

    deduped_cited_chunks: list[RetrievedChunk] = []
    seen_chunk_ids: set[str] = set()
    for chunk in cited_chunks:
        chunk_id = str(chunk.chunk_id or "").strip() or str(id(chunk))
        if chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk_id)
        deduped_cited_chunks.append(chunk)
    citation_records = _citation_records_from_chunks(deduped_cited_chunks, limit=len(deduped_cited_chunks))
    sources = [
        record.get("source_url") or f"rag:{record['chunk_id']}"
        for record in citation_records
    ]
    confidence = max(0.86, _confidence_from_chunks(deduped_cited_chunks or chunks))
    return RagAnswer(
        answer=_compose_grounded_answer_email(
            question=message,
            body="\n\n".join(section.strip() for section in sections if section.strip()),
            requester=requester,
            customer_id=customer_id,
        ),
        confidence=round(min(0.95, confidence), 2),
        sources=sources,
        citations=citation_records,
    )


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


_REQUEST_BODY_RESCUE_SECTION_RE = re.compile(
    r"(?:^|[\s\-#>])(?:\*\*)?"
    r"(Issue Description|Root Cause|Step by Step Solution|Prevention/Best Practice(?:\s*\(optional\))?|"
    r"Platform/SDK|Error Message(?:\s*\(optional\))?|Solution|Resolution|Best Practice|Notes?)"
    r"(?:\*\*)?\s*:?\s*",
    re.IGNORECASE,
)
_FENCED_CODE_BLOCK_RE = re.compile(r"```(?:[A-Za-z0-9_-]+)?\s*(.*?)```", re.DOTALL)


def _normalize_evidence_label(value: str) -> str:
    label = re.sub(r"\s*\(optional\)\s*", "", str(value or ""), flags=re.IGNORECASE)
    return " ".join(label.lower().split())


def _plain_evidence_text(value: str, *, limit: int = 360) -> str:
    text = re.split(r"\s-{3,}\s", str(value or ""), maxsplit=1)[0]
    text = _FENCED_CODE_BLOCK_RE.sub("", text)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].strip()
    return f"{clipped}..." if clipped else text[:limit]


def _raw_labeled_evidence_section(text: str, labels: set[str]) -> str:
    matches = list(_REQUEST_BODY_RESCUE_SECTION_RE.finditer(text or ""))
    normalized_labels = {_normalize_evidence_label(label) for label in labels}
    for index, match in enumerate(matches):
        if _normalize_evidence_label(str(match.group(1) or "")) not in normalized_labels:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = str(text[start:end] or "").strip(" \n\r\t-:")
        if section:
            return section
    return ""


def _extract_labeled_evidence_section(text: str, labels: set[str]) -> str:
    section = _raw_labeled_evidence_section(text, labels)
    if section:
        return _plain_evidence_text(section)
    return ""


def _extract_rescue_steps(text: str) -> list[str]:
    raw_section = _raw_labeled_evidence_section(
        text,
        {"Step by Step Solution", "Solution", "Resolution", "Best Practice"},
    )
    section = _plain_evidence_text(raw_section, limit=4000)
    if not section:
        return []
    steps: list[str] = []
    for match in re.finditer(r"(?:^|\s)\d+\.\s+(.*?)(?=(?:\s+\d+\.\s+)|$)", section):
        step = _plain_evidence_text(match.group(1), limit=220).strip()
        if step and step not in steps:
            steps.append(step)
        if len(steps) >= 3:
            break
    if steps:
        return steps
    for sentence in re.split(r"(?<=[.!?])\s+", section):
        step = _plain_evidence_text(sentence, limit=220).strip()
        if step and step not in steps:
            steps.append(step)
        if len(steps) >= 3:
            break
    return steps


def _json_path_exists(payload: Any, path: str) -> bool:
    current = payload
    parts = [part for part in str(path or "").split(".") if part]
    if not parts:
        return False
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return False
    return True


def _parent_json_path_exists(payload: Any, path: str) -> bool:
    parts = [part for part in str(path or "").split(".") if part]
    if len(parts) < 2:
        return False
    return _json_path_exists(payload, ".".join(parts[:-1]))


def _request_body_schema_paths(request_body_evidence: RequestBodyEvidenceResult | None) -> list[str]:
    if request_body_evidence is None:
        return []
    paths: list[str] = []
    for evidence_chunk in request_body_evidence.chunks:
        for field_path in evidence_chunk.matched_fields:
            normalized = str(field_path or "").strip()
            if normalized and normalized not in paths:
                paths.append(normalized)
    if paths:
        return paths
    for field_path in request_body_evidence.query.nested_paths:
        normalized = str(field_path or "").strip()
        if normalized and normalized not in paths:
            paths.append(normalized)
    return paths


def _request_body_authoritative_schema_paths(request_body_evidence: RequestBodyEvidenceResult | None) -> list[str]:
    if request_body_evidence is None:
        return []
    paths: list[str] = []
    for evidence_chunk in request_body_evidence.chunks:
        for field_path in evidence_chunk.matched_fields:
            normalized = str(field_path or "").strip()
            if normalized and normalized not in paths:
                paths.append(normalized)
    return paths


def _json_paths_by_leaf(payload: Any, leaf: str, *, prefix: str = "") -> list[str]:
    target = str(leaf or "").strip()
    if not target:
        return []
    paths: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            path = f"{prefix}.{key_text}" if prefix else key_text
            if key_text == target:
                paths.append(path)
            paths.extend(_json_paths_by_leaf(value, target, prefix=path))
    elif isinstance(payload, list):
        for value in payload:
            paths.extend(_json_paths_by_leaf(value, target, prefix=prefix))
    return paths


def _request_body_payload_conflicts_with_schema(payload: Any, schema_paths: list[str]) -> bool:
    if not isinstance(payload, dict):
        return False
    for schema_path in schema_paths:
        normalized = str(schema_path or "").strip()
        if not normalized or _json_path_exists(payload, normalized):
            continue
        leaf = normalized.split(".")[-1]
        for payload_path in _json_paths_by_leaf(payload, leaf):
            if payload_path != normalized:
                return True
    return False


_REQUEST_BODY_CORRECT_JSON_CONTEXT_RE = re.compile(
    r"\b(correct(?:ed)?|fixed|valid|recommended)\b",
    re.IGNORECASE,
)
_REQUEST_BODY_INCORRECT_JSON_CONTEXT_RE = re.compile(
    r"\b(incorrect|wrong|invalid|misplaced|outside|sibling)\b",
    re.IGNORECASE,
)


def _last_context_match_position(pattern: re.Pattern[str], text: str) -> int:
    position = -1
    for match in pattern.finditer(text):
        position = match.start()
    return position


def _score_request_body_json_block_context(text: str, block_start: int) -> int:
    context = str(text or "")[max(0, int(block_start) - 320) : int(block_start)]
    if not context:
        return 0
    correct_position = _last_context_match_position(_REQUEST_BODY_CORRECT_JSON_CONTEXT_RE, context)
    incorrect_position = _last_context_match_position(_REQUEST_BODY_INCORRECT_JSON_CONTEXT_RE, context)
    if correct_position > incorrect_position:
        return 20
    if incorrect_position > correct_position:
        return -20
    return 0


def _score_request_body_json_payload(payload: Any, schema_paths: list[str]) -> int:
    if not isinstance(payload, dict):
        return -100
    score = 0
    for path in schema_paths:
        if _json_path_exists(payload, path):
            score += 4
        elif _parent_json_path_exists(payload, path):
            score += 1
    if _json_path_exists(payload, "clientRequest.recordingConfig.transcodingConfig"):
        score += 4
    if _json_path_exists(payload, "clientRequest.transcodingConfig"):
        score -= 5
    if _json_path_exists(payload, "clientRequest.recordingConfig.transcodingConfig.layoutConfig"):
        score += 1
    if _json_path_exists(payload, "clientRequest.recordingConfig.transcodingConfig.mixedVideoLayout"):
        score += 1
    return score


def _extract_corrected_request_body_json(
    text: str,
    request_body_evidence: RequestBodyEvidenceResult | None,
) -> str:
    schema_paths = _request_body_schema_paths(request_body_evidence)
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, match in enumerate(_FENCED_CODE_BLOCK_RE.finditer(text or "")):
        raw_block = str(match.group(1) or "").strip()
        if not raw_block.startswith("{"):
            continue
        try:
            payload = json.loads(raw_block)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        score = _score_request_body_json_payload(payload, schema_paths)
        score += _score_request_body_json_block_context(text, match.start())
        if score > 0:
            candidates.append((score, -index, payload))
    if not candidates:
        return ""
    _, _, payload = max(candidates, key=lambda item: (item[0], item[1]))
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _contains_parseable_json_block(text: str) -> bool:
    fenced_text = _fence_standalone_json_blocks(text)
    for match in _FENCED_CODE_BLOCK_RE.finditer(fenced_text):
        raw_block = str(match.group(1) or "").strip()
        try:
            parsed = json.loads(raw_block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, (dict, list)):
            return True
    return False


def _contains_schema_aligned_parseable_json_block(
    text: str,
    request_body_evidence: RequestBodyEvidenceResult | None,
) -> bool:
    schema_paths = _request_body_authoritative_schema_paths(request_body_evidence)
    if not schema_paths:
        return _contains_parseable_json_block(text)
    fenced_text = _fence_standalone_json_blocks(text)
    for match in _FENCED_CODE_BLOCK_RE.finditer(fenced_text):
        raw_block = str(match.group(1) or "").strip()
        try:
            parsed = json.loads(raw_block)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if _request_body_payload_conflicts_with_schema(parsed, schema_paths):
            continue
        if _score_request_body_json_payload(parsed, schema_paths) > 0:
            return True
    return False


def _request_body_json_supplement_from_chunks(
    chunks: list[RetrievedChunk],
    request_body_evidence: RequestBodyEvidenceResult | None,
) -> str:
    if request_body_evidence is None or not request_body_evidence.triggered:
        return ""
    ordered_chunks = sorted(
        list(chunks),
        key=lambda chunk: 0 if is_high_value_troubleshooting_context(chunk) else 1,
    )
    for chunk in ordered_chunks:
        corrected_json = _extract_corrected_request_body_json(chunk.text, request_body_evidence)
        if corrected_json:
            return corrected_json
    return ""


def _supplement_request_body_json_if_missing(
    answer: str,
    chunks: list[RetrievedChunk],
    request_body_evidence: RequestBodyEvidenceResult | None,
) -> str:
    body = str(answer or "").strip()
    if not body or _contains_schema_aligned_parseable_json_block(body, request_body_evidence):
        return body
    corrected_json = _request_body_json_supplement_from_chunks(chunks, request_body_evidence)
    if not corrected_json:
        return body
    return (
        f"{body}\n\n"
        "Use this corrected request body structure from the cited evidence:\n"
        f"```json\n{corrected_json}\n```"
    )


def _is_request_body_schema_context(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    evidence_type = str(metadata.get("request_body_evidence_type") or "").strip()
    if evidence_type:
        return True
    if bool(metadata.get("request_body_skill_triggered")):
        return True
    text = _chunk_search_text(chunk).lower()
    return "request body" in text and ("schema" in text or "clientrequest" in text)


def _build_request_body_evidence_rescue_answer(
    *,
    question: str,
    chunks: list[RetrievedChunk],
    request_body_evidence: RequestBodyEvidenceResult | None,
    requester: str | None,
    customer_id: str | None,
) -> RagAnswer | None:
    if request_body_evidence is None or not request_body_evidence.triggered:
        return None

    technical_chunk = next((chunk for chunk in chunks if is_high_value_troubleshooting_context(chunk)), None)
    schema_chunk = next((chunk for chunk in chunks if _is_request_body_schema_context(chunk)), None)
    if technical_chunk is None or schema_chunk is None:
        return None

    cause = _extract_labeled_evidence_section(technical_chunk.text, {"Root Cause"})
    if not cause:
        cause = _extract_labeled_evidence_section(technical_chunk.text, {"Issue Description"})
    if not cause:
        cause = _plain_evidence_text(technical_chunk.text, limit=360)
    if not cause:
        return None

    schema_heading = _build_heading(schema_chunk)
    corrected_json = _extract_corrected_request_body_json(
        technical_chunk.text,
        request_body_evidence,
    )
    body = (
        "The request body you shared matches both a documented request-body schema and a technical "
        f"troubleshooting article. {cause}\n\n"
        f"I would use the cited `{schema_heading}` schema as the source of truth for the nested request body."
    )
    if corrected_json:
        body = (
            f"{body}\n\n"
            "A minimal corrected request body from the cited evidence is:\n"
            f"```json\n{corrected_json}\n```"
        )
    steps = _extract_rescue_steps(technical_chunk.text)
    if not steps:
        steps = [
            "Compare the submitted payload with the cited request-body schema.",
            "Move any misplaced configuration fields to the parent object shown in the schema evidence.",
            "Retest with a new API request or recording session after the request body is corrected.",
        ]

    ordered_chunks = [technical_chunk, schema_chunk]
    citations = _citation_records_from_chunks(ordered_chunks, limit=2)
    sources = [record.get("source_url") or f"rag:{record['chunk_id']}" for record in citations if record.get("chunk_id")]
    return RagAnswer(
        answer=_build_answer_text(
            body,
            steps,
            question=question,
            requester=requester,
            customer_id=customer_id,
        ),
        confidence=max(0.78, _confidence_from_chunks(ordered_chunks)),
        sources=sources or ["rag"],
        citations=citations,
    )


def _build_answer_profile(
    config: dict[str, Any],
    *,
    use_light_path_fast_model: bool = False,
    query_class: str | None = None,
) -> ModelProfile:
    defaults = resolve_model_profile(RAG_ANSWER_SCENARIO)
    model_name = str(config.get("chat_model") or "").strip() or defaults.model
    reasoning_effort = str(config.get("reasoning_effort") or "").strip() or defaults.reasoning_effort or "high"
    fallback_models = tuple(config.get("fallback_models") or defaults.fallback_models)
    if use_light_path_fast_model:
        if str(query_class or "").strip().lower() == "api_semantics_mismatch":
            model_name = _API_SEMANTICS_FAST_ANSWER_MODEL
            reasoning_effort = _API_SEMANTICS_FAST_ANSWER_REASONING_EFFORT
        else:
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
        fallback_profiles=tuple(defaults.fallback_profiles),
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
    citation_retry: bool = False,
) -> dict[str, Any] | None:
    context_block = packed_evidence.prompt_context if packed_evidence is not None else _format_context(chunks)
    prompt = _build_answer_prompt_for_mode(
        message,
        context_block,
        repair_mode=strict_retry,
        citation_retry=citation_retry,
    )
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
    citation_retry: bool = False,
) -> tuple[dict[str, Any] | None, int, int, str | None]:
    context_block = packed_evidence.prompt_context if packed_evidence is not None else _format_context(chunks)
    prompt = _build_answer_prompt_for_mode(
        message,
        context_block,
        repair_mode=strict_retry,
        citation_retry=citation_retry,
    )
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
        model_label = response.provider_model_name if response.provider_name != "openai" else response.model_name
        return payload, response.prompt_tokens, response.completion_tokens, model_label
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

    if _is_generic_join_channel_query(query or ""):
        for matcher in (_is_join_channel_step_chunk, _is_token_auth_chunk):
            for index, chunk in enumerate(chunks):
                chunk_key = _chunk_selection_key(chunk, index)
                if chunk_key in selected_chunk_keys:
                    continue
                if not matcher(chunk):
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
    ticket_context: list[dict[str, str]] | None = None,
    requester: str | None = None,
    customer_id: str | None = None,
    product: str | None = None,
    query_policy: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> RagQueryResult | None:
    config = _get_rag_config(top_k=top_k, query_policy=query_policy)
    resolved_table = _resolve_active_vector_table(config)
    if resolved_table:
        config["table"] = resolved_table
    if not config["dsn"] or not _rag_config_llm_available(config):
        return None

    provider = get_embedding_provider()
    effective_question, follow_up_inheritance = _resolve_effective_question(message, ticket_context)
    original_query = str(effective_question or "").strip()
    vector_chunks: list[RetrievedChunk] = []
    bm25_chunks: list[RetrievedChunk] = []
    keyword_fallback_chunks: list[RetrievedChunk] = []
    chunks: list[RetrievedChunk] = []
    embedding_request_meta: list[dict[str, Any]] = []
    embedding_dimensions = getattr(provider, "vector_dim", None)
    query_type = _infer_query_type(effective_question)
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
    query_variants: list[tuple[str, str]] = [("original", original_query)]
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
    request_body_evidence_result: RequestBodyEvidenceResult | None = None
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
        query_understanding_future = query_understanding_executor.submit(
            understand_rag_query,
            effective_question,
            query_policy=query_policy,
        )
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
            effective_question,
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

    weak_first_pass = (not chunks) or (not _has_grounded_keyword_overlap(effective_question, chunks)) or (
        len(chunks) < min(2, int(config["top_k"]))
    )
    if query_prf_enabled and query_understanding is not None and weak_first_pass:
        _raise_if_cancelled(
            "round_2_recovery",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        prf_expansion_terms = build_prf_expansions(
            effective_question,
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
    request_body_evidence_result = _request_body_evidence_result_for_query(
        effective_question,
        _retrieval_config_for(effective_plan),
    )
    if request_body_evidence_result is not None and request_body_evidence_result.chunks:
        chunks = _merge_request_body_evidence_into_final_chunks(
            chunks,
            request_body_evidence=request_body_evidence_result,
            max_chunks=int(config["fusion_candidate_k"]),
        )

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
            compatible_lexical_latency_ms = round(bm25_latency_ms + keyword_fallback_latency_ms, 2)
            trace = RagQueryTrace(
                query_type=query_type,
                retrieval_strategy=_retrieval_strategy_for(keyword_fallback_used=bool(keyword_fallback_chunks)),
                vector_candidates_count=len(vector_chunks),
                bm25_candidates_count=len(bm25_chunks),
                reranked_candidates_count=0,
                retrieved_chunk_ids=[],
                selected_chunk_ids=[],
                vector_retrieval_latency_ms=vector_latency_ms,
                bm25_retrieval_latency_ms=compatible_lexical_latency_ms,
                retrieval_latency_ms=round(vector_latency_ms + compatible_lexical_latency_ms, 2),
                rerank_latency_ms=0.0,
                generation_latency_ms=0.0,
                total_latency_ms=round((time.perf_counter() - total_started_at) * 1000, 2),
                prompt_tokens=0,
                completion_tokens=0,
                embedding_tokens=_estimate_embedding_tokens(effective_question),
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
                bm25_sql_latency_ms=round(bm25_latency_ms, 2),
                fts_latency_ms=0.0,
                retrieval_round_wall_clock_ms=compatible_lexical_latency_ms,
                retrieval_tool_timings=[],
                effective_question=effective_question,
                follow_up_inheritance_used=follow_up_inheritance is not None,
                follow_up_inheritance_source=(
                    str(follow_up_inheritance.source or "").strip() or None
                    if follow_up_inheritance is not None
                    else None
                ),
                **_request_body_trace_values(request_body_evidence_result),
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
            query=effective_question,
            chunks=chunks,
            top_k=int(config["fusion_candidate_k"]),
            retrieval_plan=effective_plan,
            query_understanding=query_understanding,
            query_policy=str(config.get("query_policy") or ""),
        )
        rerank_latency_ms = round((time.perf_counter() - rerank_started_at) * 1000, 2)
        chunks = reranked_chunks or chunks
        chunks = _reorder_chunks_for_rerank(
            chunks,
            limit=int(config["rerank_top_n"]),
            query=effective_question,
        ) or chunks
        rerank_started_at = time.perf_counter()
        externally_reranked = _rerank_chunks(
            effective_question,
            chunks,
            config,
            limit=int(config["rerank_top_n"]),
        )
        rerank_latency_ms = round(rerank_latency_ms + ((time.perf_counter() - rerank_started_at) * 1000), 2)
        chunks = externally_reranked or chunks

    final_chunks = _select_diverse_chunks(
        chunks,
        limit=int(config["top_k"]),
        query=effective_question,
    ) or chunks[: int(config["top_k"])] or chunks
    final_chunks = _merge_request_body_evidence_into_final_chunks(
        final_chunks,
        request_body_evidence=request_body_evidence_result,
        max_chunks=int(config["top_k"]),
        retrieved_chunks=retrieved_chunks,
    )
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
            fallback_profiles=tuple(compression_defaults.fallback_profiles),
        )
        packing_limit = min(len(chunks), max(int(config.get("top_k") or 1), int(config.get("rerank_top_n") or len(chunks))))
        packing_candidates = list(chunks[:packing_limit]) or list(chunks)
        packed_evidence = build_packed_evidence(
            question=effective_question,
            chunks=packing_candidates,
            system_prompt_text=_build_answer_system_prompt(product),
            user_prompt_text=_build_answer_prompt_for_mode(effective_question, "", repair_mode=False),
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
    grounded_overlap = _has_grounded_keyword_overlap(effective_question, final_chunks)
    generation_config = dict(config)
    generation_config["reasoning_effort"] = _effective_answer_reasoning_effort(
        base_effort=str(config.get("reasoning_effort") or ""),
        query_class=_classify_agentic_query(effective_question, query_understanding),
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
            effective_question,
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
                effective_question,
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
        resolved_query_policy = _normalize_query_policy(config.get("query_policy"))
        downpushed_hard_filters = downpush_hard_filters(
            effective_plan,
            query_policy=resolved_query_policy,
        )
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
        compatible_lexical_latency_ms = round(bm25_latency_ms + keyword_fallback_latency_ms, 2)
        answer_path_decision = None
        if _is_how_to_faq_query(effective_question, query_understanding):
            answer_path_decision = "answer_first" if not needs_human else "clarify_first"
        return RagQueryTrace(
            query_type=query_type,
            retrieval_strategy=_retrieval_strategy_for(keyword_fallback_used=bool(keyword_fallback_chunks)),
            vector_candidates_count=len(vector_chunks),
            bm25_candidates_count=len(bm25_chunks),
            reranked_candidates_count=int(rerank_info.get("post_rerank_count") or 0),
            retrieved_chunk_ids=[chunk.chunk_id for chunk in retrieved_chunks if chunk.chunk_id],
            selected_chunk_ids=selected_chunk_ids,
            vector_retrieval_latency_ms=vector_latency_ms,
            bm25_retrieval_latency_ms=compatible_lexical_latency_ms,
            retrieval_latency_ms=round(vector_latency_ms + compatible_lexical_latency_ms, 2),
            rerank_latency_ms=rerank_latency_ms,
            generation_latency_ms=generation_latency_ms,
            total_latency_ms=round((time.perf_counter() - total_started_at) * 1000, 2),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            embedding_tokens=_estimate_embedding_tokens(effective_question),
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
            query_policy=resolved_query_policy or None,
            glossary_version=query_understanding.glossary_version if query_understanding is not None else None,
            self_query_version=query_understanding.self_query_version if query_understanding is not None else None,
            fallback_mode=query_meta["fallback_mode"] or None,
            glossary_hit_terms=list(query_meta["glossary_hit_terms"]),
            applied_hard_filters=dict(query_meta["applied_hard_filters"]),
            downpushed_hard_filters=dict(downpushed_hard_filters),
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
            bm25_sql_latency_ms=round(bm25_latency_ms, 2),
            fts_latency_ms=0.0,
            retrieval_round_wall_clock_ms=compatible_lexical_latency_ms,
            retrieval_tool_timings=[],
            variant_candidate_counts={},
            variant_zero_yield_reasons=[],
            doc_family_mix=_doc_family_mix_for_chunks(final_chunks),
            answer_path_decision=answer_path_decision,
            effective_question=effective_question,
            follow_up_inheritance_used=follow_up_inheritance is not None,
            follow_up_inheritance_source=(
                str(follow_up_inheritance.source or "").strip() or None
                if follow_up_inheritance is not None
                else None
            ),
            **_request_body_trace_values(request_body_evidence_result),
        )

    if payload is not None and _is_valid_response(payload, allowed_chunk_ids):
        if payload["insufficient_evidence"] is True:
            rescue_answer = _build_request_body_evidence_rescue_answer(
                question=effective_question,
                chunks=final_chunks,
                request_body_evidence=request_body_evidence_result,
                requester=requester,
                customer_id=customer_id,
            )
            if rescue_answer is not None:
                generation_mode = "request_body_evidence_rescue"
                return RagQueryResult(
                    answer=rescue_answer,
                    trace=_trace_for(
                        rescue_answer,
                        needs_human=False,
                        handoff_reason=None,
                        generation_mode=generation_mode,
                        extractive_fallback_used=False,
                    ),
                )
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
                        needs_human=True,
                        handoff_reason="insufficient_evidence",
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
        answer_body = _supplement_request_body_json_if_missing(
            str(payload["answer"]),
            final_chunks,
            request_body_evidence_result,
        )
        answer_body, citations = _supplement_how_to_code_example_if_missing(
            answer_body,
            question=effective_question,
            chunks=final_chunks,
            citation_ids=citations,
        )
        citation_records = _citation_records_from_ids(citations, final_chunks)
        sources = [
            record.get("source_url") or f"rag:{record['chunk_id']}"
            for record in citation_records
        ]
        answer = RagAnswer(
            answer=_build_answer_text(
                answer_body,
                payload.get("key_steps", []),
                question=effective_question,
                requester=requester,
                customer_id=customer_id,
            ),
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
    rescue_answer = _build_request_body_evidence_rescue_answer(
        question=effective_question,
        chunks=final_chunks,
        request_body_evidence=request_body_evidence_result,
        requester=requester,
        customer_id=customer_id,
    )
    if rescue_answer is not None:
        generation_mode = "request_body_evidence_rescue"
        return RagQueryResult(
            answer=rescue_answer,
            trace=_trace_for(
                rescue_answer,
                needs_human=False,
                handoff_reason=None,
                generation_mode=generation_mode,
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
            needs_human=True,
            handoff_reason="insufficient_evidence",
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
        "shadow_tools_skipped": list(iteration.shadow_tools_skipped),
        "query_variants": list(iteration.query_variants),
        "selected_chunk_ids": list(iteration.selected_chunk_ids),
        "decision": str(iteration.decision),
        "recovery_action": iteration.recovery_action,
    }


def _classify_agentic_query_flags(effective_question: str) -> AgenticQueryFlags:
    preliminary_query_class = _classify_agentic_query(effective_question, None)
    api_semantics_query = preliminary_query_class == "api_semantics_mismatch"
    usage_configuration_query = preliminary_query_class == "usage_configuration"
    short_how_to_faq_query = preliminary_query_class == "usage_configuration" and _is_short_how_to_faq_query(effective_question)
    simple_lexical_query = preliminary_query_class == "lexical_exact" and _is_simple_lexical_query(effective_question)
    vector_setup_skipped = simple_lexical_query or usage_configuration_query or api_semantics_query
    light_path_used = simple_lexical_query or short_how_to_faq_query or api_semantics_query
    skip_bm25_warmup = usage_configuration_query or api_semantics_query
    return AgenticQueryFlags(
        preliminary_query_class=preliminary_query_class,
        api_semantics_query=api_semantics_query,
        short_how_to_faq_query=short_how_to_faq_query,
        simple_lexical_query=simple_lexical_query,
        vector_setup_skipped=vector_setup_skipped,
        light_path_used=light_path_used,
        skip_bm25_warmup=skip_bm25_warmup,
    )


def _resolve_agentic_feature_flags(
    *,
    config: dict[str, Any],
    query_flags: AgenticQueryFlags,
    effective_question: str,
) -> AgenticFeatureFlags:
    short_symptom_troubleshooting_query = _is_short_symptom_troubleshooting_query(effective_question)
    warm_vector_enabled = bool(config.get("vector_enabled")) and not (
        query_flags.simple_lexical_query
        or query_flags.short_how_to_faq_query
        or query_flags.api_semantics_query
        or _is_generic_join_channel_query(effective_question)
        or short_symptom_troubleshooting_query
    )
    query_understanding_enabled = _feature_flag_enabled("RAG_QUERY_UNDERSTANDING_ENABLED", True) and not (
        query_flags.simple_lexical_query or query_flags.api_semantics_query
    )
    query_rewrite_enabled = _feature_flag_enabled("RAG_QUERY_REWRITE_ENABLED", True) and not (
        query_flags.api_semantics_query
    )
    query_decomposition_enabled = _feature_flag_enabled("RAG_QUERY_DECOMPOSITION_ENABLED", True) and not (
        query_flags.api_semantics_query
    )
    query_expansion_enabled = _feature_flag_enabled("RAG_QUERY_EXPANSION_ENABLED", True) and not (
        query_flags.api_semantics_query
    )
    return AgenticFeatureFlags(
        query_understanding_enabled=query_understanding_enabled,
        query_rewrite_enabled=query_rewrite_enabled,
        query_decomposition_enabled=query_decomposition_enabled,
        query_expansion_enabled=query_expansion_enabled,
        warm_vector_enabled=warm_vector_enabled,
    )


def _build_warm_seed_tool_results(
    warm_original_vector_chunks: list[RetrievedChunk],
    warm_original_bm25_chunks: list[RetrievedChunk],
) -> dict[str, list[RetrievedChunk]]:
    return {
        tool_name: chunks
        for tool_name, chunks in {
            "p_vec": warm_original_vector_chunks,
            "p_bm25": warm_original_bm25_chunks,
        }.items()
        if chunks
    }


def _should_recover_agentic_round(judge: AgenticJudgeDecision, round_index: int) -> bool:
    return judge.decision == "recover_once" and round_index == 1


def _run_rag_query_agentic_single(
    message: str,
    top_k: int | None = None,
    *,
    ticket_context: list[dict[str, str]] | None = None,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    requester: str | None = None,
    product: str | None = None,
    query_policy: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> RagQueryResult | None:
    _ = ticket_id
    request_started_at = time.perf_counter()
    config = _get_rag_config(top_k=top_k, query_policy=query_policy)
    if not config["dsn"] or not _rag_config_llm_available(config):
        return None

    def _positive_seconds(value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = float(default)
        if parsed <= 0:
            parsed = float(default)
        return max(0.001, parsed)

    total_deadline_seconds = _positive_seconds(
        config.get("request_timeout_seconds"),
        resolve_model_profile(RAG_ANSWER_SCENARIO).timeout_seconds,
    )
    query_stage_timeout_seconds = min(
        total_deadline_seconds,
        _positive_seconds(
            resolve_model_profile(QUERY_EXPANSION_SCENARIO).timeout_seconds,
            total_deadline_seconds,
        ),
    )
    deadline = RagDeadline(
        started_at=request_started_at,
        total_seconds=total_deadline_seconds,
        stage_timeout_seconds={
            "query_understanding": query_stage_timeout_seconds,
            "warm_original_vector": query_stage_timeout_seconds,
            "warm_original_bm25": query_stage_timeout_seconds,
        },
    )

    def _deadline_exhausted(stage: str) -> bool:
        if deadline.is_exhausted():
            deadline.mark_timeout(stage)
            return True
        return False

    effective_question, follow_up_inheritance = _resolve_effective_question(message, ticket_context)
    query_type = _infer_query_type(effective_question)
    shadow_retrieval_enabled = _shadow_retrieval_enabled(config)
    query_flags = _classify_agentic_query_flags(effective_question)
    api_semantics_query = query_flags.api_semantics_query
    short_how_to_faq_query = query_flags.short_how_to_faq_query
    simple_lexical_query = query_flags.simple_lexical_query
    vector_setup_skipped = query_flags.vector_setup_skipped
    light_path_used = query_flags.light_path_used
    skip_bm25_warmup = query_flags.skip_bm25_warmup
    preflight_probe_latency_ms = 0.0

    config["_vector_runtime_available"] = (
        False
        if (simple_lexical_query or api_semantics_query)
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
    feature_flags = _resolve_agentic_feature_flags(
        config=config,
        query_flags=query_flags,
        effective_question=effective_question,
    )
    warm_vector_enabled = feature_flags.warm_vector_enabled
    query_understanding_enabled = feature_flags.query_understanding_enabled
    query_rewrite_enabled = feature_flags.query_rewrite_enabled
    query_decomposition_enabled = feature_flags.query_decomposition_enabled
    query_expansion_enabled = feature_flags.query_expansion_enabled
    defer_usage_configuration_understanding = (
        query_flags.preliminary_query_class == "usage_configuration"
        and query_understanding_enabled
    )
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
    fts_latency_ms = 0.0
    keyword_fallback_latency_ms = 0.0
    retrieval_round_wall_clock_ms = 0.0
    retrieval_tool_timings: list[dict[str, Any]] = []
    shadow_tools_skipped: list[str] = []
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
    request_body_evidence_result: RequestBodyEvidenceResult | None = None
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
    lexical_result_cache: dict[tuple[str, str, str, int], tuple[str, list[RetrievedChunk]]] = {}

    def _timed_retrieve(
        retrieval_fn: Any,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[list[RetrievedChunk], float]:
        started_at = time.perf_counter()
        chunks = retrieval_fn(*args, **kwargs)
        return list(chunks or []), round((time.perf_counter() - started_at) * 1000, 2)

    if query_understanding_enabled and not defer_usage_configuration_understanding:
        _raise_if_cancelled(
            "query_understanding",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        query_understanding_executor = ThreadPoolExecutor(max_workers=3 if warm_vector_enabled else 2)
        warm_retrieval_config = dict(config)
        warm_retrieval_config["_retrieval_plan"] = RetrievalPlan(semantic_query=str(effective_question or "").strip())
        query_understanding_future = query_understanding_executor.submit(
            understand_rag_query,
            effective_question,
            query_policy=query_policy,
        )
        if warm_vector_enabled:
            warm_original_vector_future = query_understanding_executor.submit(
                _timed_retrieve,
                _retrieve_chunks,
                effective_question,
                warm_retrieval_config,
                limit=int(config.get("vector_candidate_k") or config.get("top_k") or 5),
                index_role="primary",
            )
        if not skip_bm25_warmup:
            warm_original_bm25_future = query_understanding_executor.submit(
                _timed_retrieve,
                _retrieve_bm25_chunks,
                effective_question,
                warm_retrieval_config,
                limit=int(config.get("bm25_candidate_k") or config.get("top_k") or 5),
                index_role="primary",
            )
    sidecar_future_timed_out = False

    def _wait_for_sidecar_future(future: Future[Any], stage: str, *, wait_if_pending: bool = True) -> Any | None:
        nonlocal sidecar_future_timed_out
        if not wait_if_pending and not future.done():
            future.cancel()
            return None
        timeout_seconds = deadline.remaining_seconds(stage)
        if timeout_seconds <= 0:
            deadline.mark_timeout(stage)
            future.cancel()
            sidecar_future_timed_out = True
            return None
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            deadline.mark_timeout(stage)
            future.cancel()
            sidecar_future_timed_out = True
            logger.warning("RAG %s timed out after %.3fs", stage, timeout_seconds)
            return None

    def _apply_query_understanding_result(source: QueryUnderstandingResult) -> None:
        nonlocal query_understanding
        nonlocal effective_hard_filters, effective_soft_signals
        nonlocal effective_rule_expansions, effective_llm_expansions
        nonlocal effective_rewrites, effective_decomposition_subqueries
        nonlocal effective_plan, effective_query_understanding

        query_understanding = source
        effective_hard_filters = dict(source.retrieval_plan.hard_filters)
        effective_soft_signals = dict(source.retrieval_plan.soft_signals)
        effective_rule_expansions = list(source.retrieval_plan.rule_expansions) if query_expansion_enabled else []
        effective_llm_expansions = (
            list(source.retrieval_plan.llm_expansions or source.rewritten_queries)
            if query_rewrite_enabled
            else []
        )
        effective_rewrites = list(effective_llm_expansions)
        effective_decomposition_subqueries = (
            list(source.decomposition_subqueries) if query_decomposition_enabled else []
        )
        effective_plan = RetrievalPlan(
            semantic_query=source.semantic_query or str(effective_question or "").strip(),
            hard_filters=dict(effective_hard_filters),
            soft_signals=dict(effective_soft_signals),
            rewritten_queries=list(effective_rewrites),
            decomposition_subqueries=list(effective_decomposition_subqueries),
            fallback_mode=source.fallback_mode,
            rule_expansions=list(effective_rule_expansions),
            llm_expansions=list(effective_llm_expansions),
            prf_expansions=list(effective_prf_expansions),
            hard_filter_sources=dict(source.retrieval_plan.hard_filter_sources),
            soft_signal_sources=dict(source.retrieval_plan.soft_signal_sources),
            cache_hit=bool(source.cache_hit),
            prf_used=bool(source.retrieval_plan.prf_used),
        )
        effective_query_understanding = replace(
            source,
            rewritten_queries=list(effective_rewrites),
            decomposition_subqueries=list(effective_decomposition_subqueries),
            retrieval_plan=effective_plan,
        )

    if query_understanding_future is not None:
        query_understanding_timed_out = False
        try:
            query_understanding_result = _wait_for_sidecar_future(
                query_understanding_future,
                "query_understanding",
            )
            if isinstance(query_understanding_result, QueryUnderstandingResult):
                query_understanding = query_understanding_result
            elif deadline.timeout_stage == "query_understanding":
                query_understanding_timed_out = True
        except Exception as exc:
            logger.warning("RAG query understanding failed: %s", exc)
        wait_for_warm_retrieval = not query_understanding_timed_out
        if warm_original_vector_future is not None:
            try:
                warm_vector_result = _wait_for_sidecar_future(
                    warm_original_vector_future,
                    "warm_original_vector",
                    wait_if_pending=wait_for_warm_retrieval,
                )
                if warm_vector_result is not None:
                    warm_original_vector_chunks, warm_original_vector_latency_ms = warm_vector_result
            except Exception as exc:
                config["_vector_runtime_available"] = False
                logger.warning("Agentic warm vector retrieval failed: %s", exc)
        if warm_original_bm25_future is not None:
            try:
                warm_bm25_result = _wait_for_sidecar_future(
                    warm_original_bm25_future,
                    "warm_original_bm25",
                    wait_if_pending=wait_for_warm_retrieval,
                )
                if warm_bm25_result is not None:
                    warm_original_bm25_chunks, warm_original_bm25_latency_ms = warm_bm25_result
            except Exception as exc:
                logger.warning("Agentic warm BM25 retrieval failed: %s", exc)
        if query_understanding_executor is not None:
            query_understanding_executor.shutdown(
                wait=not sidecar_future_timed_out,
                cancel_futures=sidecar_future_timed_out,
            )
    if query_understanding is not None:
        _apply_query_understanding_result(query_understanding)
    else:
        effective_plan = RetrievalPlan(semantic_query=str(effective_question or "").strip())

    plan = _build_agentic_retrieval_plan(
        message=effective_question,
        top_k=int(config["top_k"]),
        query_understanding=effective_query_understanding or query_understanding,
        ticket_context=ticket_context,
        product=product,
        shadow_retrieval_enabled=shadow_retrieval_enabled,
        should_cancel=should_cancel,
        record_cancel_stage=record_cancel_stage,
    )
    if plan.query_class == "api_semantics_mismatch":
        config = _apply_api_semantics_latency_budget(config)
        light_path_used = True
    elif plan.light_path:
        config = _apply_light_path_latency_budget(config)
        light_path_used = True
    shadow_tools_skipped = list(plan.shadow_tools_skipped)
    warm_seed_tool_results = _build_warm_seed_tool_results(
        warm_original_vector_chunks,
        warm_original_bm25_chunks,
    )

    for round_index in [1, 2]:
        if round_index == 2:
            if (
                recovery_action == "configuration_recovery"
                and query_understanding is None
                and query_understanding_enabled
            ):
                try:
                    _raise_if_cancelled(
                        "query_understanding",
                        should_cancel=should_cancel,
                        record_stage=record_cancel_stage,
                    )
                    lazy_executor = ThreadPoolExecutor(max_workers=1)
                    lazy_future_timed_out = False
                    lazy_future = lazy_executor.submit(
                        understand_rag_query,
                        effective_question,
                        query_policy=query_policy,
                    )
                    try:
                        lazy_understanding = _wait_for_sidecar_future(lazy_future, "query_understanding")
                        lazy_future_timed_out = bool(sidecar_future_timed_out)
                    finally:
                        lazy_executor.shutdown(
                            wait=not lazy_future_timed_out,
                            cancel_futures=lazy_future_timed_out,
                        )
                    if isinstance(lazy_understanding, QueryUnderstandingResult):
                        _apply_query_understanding_result(lazy_understanding)
                        plan = _build_agentic_retrieval_plan(
                            message=effective_question,
                            top_k=int(config["top_k"]),
                            query_understanding=effective_query_understanding or query_understanding,
                            ticket_context=ticket_context,
                            product=product,
                            shadow_retrieval_enabled=shadow_retrieval_enabled,
                            should_cancel=should_cancel,
                            record_cancel_stage=record_cancel_stage,
                        )
                        shadow_tools_skipped = list(plan.shadow_tools_skipped)
                except Exception as exc:
                    logger.warning("RAG lazy query understanding for configuration recovery failed: %s", exc)
            _raise_if_cancelled(
                "round_2_recovery",
                should_cancel=should_cancel,
                record_stage=record_cancel_stage,
            )
        if _deadline_exhausted(f"round_{round_index}_retrieval"):
            final_judge = AgenticJudgeDecision(
                decision="escalate",
                reason="deadline_exhausted",
                confidence=0.0,
            )
            break
        previous_reranked_chunks = list(reranked_chunks)
        previous_final_chunks = list(final_chunks)
        previous_rerank_info = dict(final_rerank_info)
        previous_judge = final_judge
        round_result = _execute_agentic_round(
            message=effective_question,
            config=config,
            plan=plan,
            round_index=round_index,
            retrieval_plan=effective_plan,
            query_understanding=effective_query_understanding or query_understanding,
            ticket_context=ticket_context,
            recovery_action=recovery_action,
            seed_tool_results=warm_seed_tool_results if round_index == 1 else None,
            lexical_result_cache=lexical_result_cache,
            deadline=deadline,
            should_cancel=should_cancel,
            record_cancel_stage=record_cancel_stage,
        )
        if round_index == 1 and "p_vec" in round_result.used_seed_tools:
            vector_latency_ms += warm_original_vector_latency_ms
        if round_index == 1 and "p_bm25" in round_result.used_seed_tools:
            bm25_latency_ms += warm_original_bm25_latency_ms
        if round_index == 1 and "p_bm25" in round_result.used_seed_tools:
            retrieval_tool_timings.append(
                {
                    "tool_name": "p_bm25",
                    "query_kind": "original",
                    "round_index": round_index,
                    "index_role": "primary",
                    "latency_ms": round(warm_original_bm25_latency_ms, 2),
                    "candidate_count": len(warm_original_bm25_chunks),
                    "used_seed_tool": True,
                    "used_cached_tool": False,
                }
            )
        vector_latency_ms += round_result.vector_latency_ms
        bm25_latency_ms += round_result.bm25_latency_ms
        fts_latency_ms += round_result.fts_latency_ms
        keyword_fallback_latency_ms += round_result.keyword_latency_ms
        retrieval_round_wall_clock_ms += round_result.retrieval_wall_clock_ms
        retrieval_tool_timings.extend(list(round_result.retrieval_tool_timings))
        shadow_tools_skipped.extend(list(round_result.shadow_tools_skipped))
        rerank_latency_ms += round_result.rerank_latency_ms
        total_vector_candidates += round_result.vector_candidate_count
        total_bm25_candidates += round_result.bm25_candidate_count
        _merge_retrieved_chunk_map(retrieved_chunk_map, round_result.retrieved_chunks)
        reranked_chunks = list(round_result.reranked_chunks)
        final_chunks = list(round_result.final_chunks)
        final_rerank_info = dict(round_result.rerank_info)
        final_judge = round_result.judge
        if not final_chunks and previous_final_chunks:
            reranked_chunks = previous_reranked_chunks
            final_chunks = previous_final_chunks
            final_rerank_info = previous_rerank_info
            if final_judge is None or final_judge.decision == "escalate":
                final_judge = previous_judge
        agent_iterations.append(_iteration_trace_payload(round_result.iteration_trace))
        if round_index == 1:
            first_pass_candidate_count = len(round_result.retrieved_chunks)
        second_pass_candidate_count = len(round_result.retrieved_chunks)
        if _should_recover_agentic_round(round_result.judge, round_index):
            recovery_action = round_result.judge.recovery_action
            continue
        break

    retrieved_chunks = list(retrieved_chunk_map.values())
    packed_evidence: PackedEvidence | None = None
    request_body_evidence_result = _request_body_evidence_result_for_query(effective_question, config)
    if request_body_evidence_result is not None and request_body_evidence_result.chunks:
        evidence_original_chunks = [
            item.original_chunk
            for item in request_body_evidence_result.chunks
            if isinstance(item.original_chunk, RetrievedChunk)
        ]
        _merge_retrieved_chunk_map(retrieved_chunk_map, evidence_original_chunks)
        retrieved_chunks = list(retrieved_chunk_map.values())
        final_chunks = _merge_request_body_evidence_into_final_chunks(
            final_chunks,
            request_body_evidence=request_body_evidence_result,
            max_chunks=int(config["top_k"]),
            retrieved_chunks=retrieved_chunks,
        )
        if final_judge is None or final_judge.decision == "escalate":
            final_judge = AgenticJudgeDecision(
                decision="answer_now",
                reason="request_body_schema_evidence",
                confidence=max(0.65, float(request_body_evidence_result.query.confidence or 0.0)),
            )

    def _trace_for(
        answer: RagAnswer,
        *,
        needs_human: bool,
        handoff_reason: str | None,
        generation_mode: str,
        extractive_fallback_used: bool,
    ) -> RagQueryTrace:
        query_meta = _query_understanding_meta(effective_query_understanding or query_understanding, final_rerank_info)
        resolved_query_policy = _normalize_query_policy(config.get("query_policy"))
        downpushed_hard_filters = downpush_hard_filters(
            effective_plan,
            query_policy=resolved_query_policy,
        )
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
        generic_join_primary_chunk_found = False
        generic_join_support_pair_found = False
        generic_join_support_chunks: list[str] = []
        generic_join_recovery_used = False
        if _is_generic_join_channel_query(effective_question):
            join_chunk, auth_chunk = _select_generic_join_grounding_chunks(
                final_chunks,
                product=product,
                query=effective_question,
            )
            generic_join_primary_chunk_found = join_chunk is not None
            generic_join_support_pair_found = join_chunk is not None and auth_chunk is not None
            generic_join_support_chunks = [
                str(chunk.chunk_id or "").strip()
                for chunk in [join_chunk, auth_chunk]
                if chunk is not None and str(chunk.chunk_id or "").strip()
            ]
            generic_join_recovery_used = any(
                isinstance(timing, dict)
                and str(timing.get("query_kind") or "").strip() in _GENERIC_JOIN_FOCUSED_VARIANT_KINDS
                for timing in retrieval_tool_timings
            ) or any(
                any(str(source or "").startswith("generic_join_") for source in (chunk.retrieval_sources or []))
                for chunk in final_chunks
            )
        compatible_lexical_latency_ms = round(bm25_latency_ms + fts_latency_ms + keyword_fallback_latency_ms, 2)
        primary_shadow_mix = {
            "primary": sum(1 for chunk in final_chunks if str(chunk.index_role or "").strip().lower() == "primary"),
            "shadow": sum(1 for chunk in final_chunks if str(chunk.index_role or "").strip().lower() == "shadow"),
        }
        answer_path_decision = None
        if plan.query_class in {"how_to_faq", "configuration", "usage_configuration"}:
            answer_path_decision = "answer_first" if not needs_human else "clarify_first"
        return RagQueryTrace(
            query_type=query_type,
            retrieval_strategy="agentic_multi_tool_v1",
            vector_candidates_count=total_vector_candidates,
            bm25_candidates_count=total_bm25_candidates,
            reranked_candidates_count=int(final_rerank_info.get("post_rerank_count") or 0),
            retrieved_chunk_ids=[chunk.chunk_id for chunk in retrieved_chunks if chunk.chunk_id],
            selected_chunk_ids=selected_chunk_ids,
            vector_retrieval_latency_ms=round(vector_latency_ms, 2),
            bm25_retrieval_latency_ms=compatible_lexical_latency_ms,
            retrieval_latency_ms=round(vector_latency_ms + compatible_lexical_latency_ms, 2),
            rerank_latency_ms=round(rerank_latency_ms, 2),
            generation_latency_ms=round(generation_latency_ms, 2),
            total_latency_ms=round((time.perf_counter() - total_started_at) * 1000, 2),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            embedding_tokens=_estimate_embedding_tokens(effective_question),
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
            query_policy=resolved_query_policy or None,
            glossary_version=query_understanding.glossary_version if query_understanding is not None else None,
            self_query_version=query_understanding.self_query_version if query_understanding is not None else None,
            fallback_mode=query_meta["fallback_mode"] or None,
            glossary_hit_terms=list(query_meta["glossary_hit_terms"]),
            applied_hard_filters=dict(query_meta["applied_hard_filters"]),
            downpushed_hard_filters=dict(downpushed_hard_filters),
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
            first_pass_tools=list(plan.first_pass_tools),
            plan_query_variants=[
                {"kind": str(kind), "query": str(query)}
                for kind, query in plan.query_variants
                if str(query or "").strip()
            ],
            plan_decomposition_targets=list(plan.decomposition_targets),
            evidence_goal=plan.evidence_goal,
            recovery_bias=plan.recovery_bias,
            judge_summary={
                "decision": str(final_judge.decision) if final_judge is not None else None,
                "reason": str(final_judge.reason) if final_judge is not None else None,
                "recovery_action": (
                    str(final_judge.recovery_action) if final_judge is not None and final_judge.recovery_action else None
                ),
                "confidence": (
                    round(float(final_judge.confidence), 4) if final_judge is not None else None
                ),
            },
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
            shadow_retrieval_enabled=shadow_retrieval_enabled,
            shadow_tools_skipped=list(dict.fromkeys(shadow_tools_skipped)),
            bm25_sql_latency_ms=round(bm25_latency_ms, 2),
            fts_latency_ms=round(fts_latency_ms, 2),
            retrieval_round_wall_clock_ms=round(retrieval_round_wall_clock_ms, 2),
            retrieval_tool_timings=list(retrieval_tool_timings),
            variant_candidate_counts=_variant_candidate_counts_from_timings(retrieval_tool_timings),
            variant_zero_yield_reasons=_variant_zero_yield_reasons_from_timings(retrieval_tool_timings),
            deadline_exhausted=deadline.is_exhausted(),
            timeout_stage=deadline.timeout_stage,
            anchor_hits=extract_anchor_hits(message),
            doc_family_mix=_doc_family_mix_for_chunks(final_chunks),
            generic_join_primary_chunk_found=generic_join_primary_chunk_found,
            generic_join_support_pair_found=generic_join_support_pair_found,
            generic_join_support_chunks=generic_join_support_chunks,
            generic_join_recovery_used=generic_join_recovery_used,
            answer_path_decision=answer_path_decision,
            effective_question=effective_question,
            follow_up_inheritance_used=follow_up_inheritance is not None,
            follow_up_inheritance_source=(
                str(follow_up_inheritance.source or "").strip() or None
                if follow_up_inheritance is not None
                else None
            ),
            **_request_body_trace_values(request_body_evidence_result),
        )

    def _deadline_handoff_result(stage: str) -> RagQueryResult:
        deadline.mark_timeout(stage)
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
                handoff_reason="deadline_exhausted",
                generation_mode="insufficient_evidence",
                extractive_fallback_used=False,
            ),
        )

    def _deterministic_answer_result() -> RagQueryResult | None:
        nonlocal generation_latency_ms, answer_profile_used
        if not final_chunks:
            return None

        deterministic_generation_started_at = time.perf_counter()
        deterministic_answer: RagAnswer | None = None
        generation_mode: str | None = None
        if plan.query_class == "api_semantics_mismatch":
            deterministic_answer = _build_api_semantics_grounded_answer(
                effective_question,
                final_chunks,
                requester=requester,
                customer_id=customer_id,
            )
            generation_mode = "api_semantics_deterministic" if deterministic_answer is not None else None
        elif _allows_release_note_guidance_for_short_symptom_query(effective_question):
            deterministic_answer = _build_black_screen_guidance_grounded_answer(
                effective_question,
                final_chunks,
                product=product,
                requester=requester,
                customer_id=customer_id,
            )
            generation_mode = "black_screen_guidance_deterministic" if deterministic_answer is not None else None
        elif _is_generic_join_channel_query(effective_question):
            deterministic_answer = _build_generic_join_grounded_answer(
                effective_question,
                final_chunks,
                product=product,
                requester=requester,
                customer_id=customer_id,
            )
            generation_mode = "generic_join_deterministic" if deterministic_answer is not None else None

        if deterministic_answer is None or generation_mode is None:
            return None
        generation_latency_ms = (time.perf_counter() - deterministic_generation_started_at) * 1000
        answer_profile_used = generation_mode
        return RagQueryResult(
            answer=deterministic_answer,
            trace=_trace_for(
                deterministic_answer,
                needs_human=False,
                handoff_reason=None,
                generation_mode=generation_mode,
                extractive_fallback_used=False,
            ),
        )

    deterministic_result = _deterministic_answer_result()
    if deterministic_result is not None:
        return deterministic_result

    if deadline.is_exhausted():
        return _deadline_handoff_result("answer_generation")

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
    grounded_overlap = _has_grounded_keyword_overlap(effective_question, final_chunks)
    generation_config = dict(config)
    generation_config["reasoning_effort"] = _effective_answer_reasoning_effort(
        base_effort=str(config.get("reasoning_effort") or ""),
        query_class=plan.query_class,
        query_type=query_type,
    )
    generation_chunk_limit = _generation_chunk_limit_for_agentic_query(
        message=effective_question,
        plan=plan,
        config=generation_config,
    )
    if generation_chunk_limit is not None:
        final_chunks = list(final_chunks[:generation_chunk_limit])
        allowed_chunk_ids = {chunk.chunk_id for chunk in final_chunks if chunk.chunk_id}
        grounded_overlap = _has_grounded_keyword_overlap(effective_question, final_chunks)
    if final_chunks and bool(config.get("context_budget_enabled")):
        if deadline.is_exhausted():
            return _deadline_handoff_result("answer_generation")
        _raise_if_cancelled(
            "answer_generation",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        compression_defaults = resolve_model_profile(RAG_CONTEXT_COMPRESSION_SCENARIO)
        compression_timeout_seconds = min(
            float(compression_defaults.timeout_seconds),
            max(0.001, deadline.remaining_seconds("answer_generation")),
        )
        compression_profile = ModelProfile(
            scenario=RAG_CONTEXT_COMPRESSION_SCENARIO,
            provider="openai",
            model=str(config.get("context_compression_model") or "").strip() or compression_defaults.model,
            api_mode="openai_responses",
            api_key=str(config.get("api_key") or "").strip() or compression_defaults.api_key,
            reasoning_effort=str(config.get("context_compression_reasoning_effort") or "").strip()
            or compression_defaults.reasoning_effort,
            temperature=0.0,
            timeout_seconds=compression_timeout_seconds,
            max_retries=compression_defaults.max_retries,
            fallback_models=tuple(compression_defaults.fallback_models),
            fallback_profiles=tuple(compression_defaults.fallback_profiles),
        )
        packed_evidence = build_packed_evidence(
            question=effective_question,
            chunks=list(final_chunks),
            system_prompt_text=_build_answer_system_prompt(product),
            user_prompt_text=_build_answer_prompt_for_mode(effective_question, "", repair_mode=False),
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
            grounded_overlap = _has_grounded_keyword_overlap(effective_question, final_chunks)
    payload: dict[str, Any] | None = None
    generation_started_at = time.perf_counter()
    api_semantics_fast_path = plan.query_class == "api_semantics_mismatch" and final_judge.decision == "answer_now"
    fast_answer_profile = (
        _build_answer_profile(
            generation_config,
            use_light_path_fast_model=True,
            query_class=plan.query_class,
        )
        if plan.light_path and final_judge.decision == "answer_now"
        else None
    )
    primary_answer_profile = _build_answer_profile(generation_config, query_class=plan.query_class)
    def _profile_with_remaining_budget(profile: ModelProfile) -> ModelProfile | None:
        if deadline.is_exhausted():
            return None
        remaining_timeout_seconds = deadline.remaining_seconds("answer_generation")
        if remaining_timeout_seconds <= 0:
            deadline.mark_timeout("answer_generation")
            return None
        return replace(
            profile,
            timeout_seconds=min(float(profile.timeout_seconds), max(0.001, remaining_timeout_seconds)),
        )

    _raise_if_cancelled(
        "answer_generation",
        should_cancel=should_cancel,
        record_stage=record_cancel_stage,
    )
    initial_profile = fast_answer_profile or primary_answer_profile
    initial_profile_with_deadline = _profile_with_remaining_budget(initial_profile)
    if initial_profile_with_deadline is None:
        return _deadline_handoff_result("answer_generation")
    payload, prompt_tokens, completion_tokens, model_name = _invoke_llm_payload_with_trace(
        effective_question,
        final_chunks,
        generation_config,
        strict_retry=False,
        packed_evidence=packed_evidence,
        product=product,
        profile_override=initial_profile_with_deadline,
    )
    answer_profile_used = model_name or initial_profile_with_deadline.model
    retry_required = (
        payload is None
        or not _is_valid_response(payload, allowed_chunk_ids)
        or (payload.get("insufficient_evidence") is True and grounded_overlap)
    )
    if retry_required and fast_answer_profile is not None and not api_semantics_fast_path:
        answer_profile_fallback_used = True
        _raise_if_cancelled(
            "answer_generation",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        primary_profile_with_deadline = _profile_with_remaining_budget(primary_answer_profile)
        if primary_profile_with_deadline is None:
            return _deadline_handoff_result("answer_generation")
        retry_payload, retry_prompt_tokens, retry_completion_tokens, retry_model_name = _invoke_llm_payload_with_trace(
            effective_question,
            final_chunks,
            generation_config,
            strict_retry=False,
            packed_evidence=packed_evidence,
            product=product,
            profile_override=primary_profile_with_deadline,
        )
        prompt_tokens += retry_prompt_tokens
        completion_tokens += retry_completion_tokens
        model_name = retry_model_name or model_name
        answer_profile_used = model_name or primary_profile_with_deadline.model
        payload = retry_payload
        retry_required = (
            payload is None
            or not _is_valid_response(payload, allowed_chunk_ids)
            or (payload.get("insufficient_evidence") is True and grounded_overlap)
        )
    if retry_required and not api_semantics_fast_path:
        structured_retry_used = True
        _raise_if_cancelled(
            "answer_generation",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        primary_profile_with_deadline = _profile_with_remaining_budget(primary_answer_profile)
        if primary_profile_with_deadline is None:
            return _deadline_handoff_result("answer_generation")
        retry_payload, retry_prompt_tokens, retry_completion_tokens, retry_model_name = _invoke_llm_payload_with_trace(
            effective_question,
            final_chunks,
            generation_config,
            strict_retry=True,
            packed_evidence=packed_evidence,
            product=product,
            profile_override=primary_profile_with_deadline,
        )
        prompt_tokens += retry_prompt_tokens
        completion_tokens += retry_completion_tokens
        model_name = retry_model_name or model_name
        answer_profile_used = model_name or primary_profile_with_deadline.model
        payload = retry_payload
    if (
        not api_semantics_fast_path
        and payload is not None
        and _is_valid_response(payload, allowed_chunk_ids)
        and _requires_howto_citation_retry(
        message=effective_question,
        product=product,
        chunks=final_chunks,
        payload=payload,
        )
    ):
        if fast_answer_profile is not None:
            answer_profile_fallback_used = True
        _raise_if_cancelled(
            "answer_generation",
            should_cancel=should_cancel,
            record_stage=record_cancel_stage,
        )
        primary_profile_with_deadline = _profile_with_remaining_budget(primary_answer_profile)
        if primary_profile_with_deadline is None:
            return _deadline_handoff_result("answer_generation")
        retry_payload, retry_prompt_tokens, retry_completion_tokens, retry_model_name = _invoke_llm_payload_with_trace(
            effective_question,
            final_chunks,
            generation_config,
            strict_retry=True,
            packed_evidence=packed_evidence,
            product=product,
            profile_override=primary_profile_with_deadline,
            citation_retry=True,
        )
        prompt_tokens += retry_prompt_tokens
        completion_tokens += retry_completion_tokens
        if retry_payload is not None and _is_valid_response(retry_payload, allowed_chunk_ids):
            model_name = retry_model_name or model_name
            answer_profile_used = model_name or primary_profile_with_deadline.model
            payload = retry_payload
    generation_latency_ms = (time.perf_counter() - generation_started_at) * 1000

    if payload is not None and _is_valid_response(payload, allowed_chunk_ids):
        if payload["insufficient_evidence"] is True:
            rescue_answer = _build_request_body_evidence_rescue_answer(
                question=effective_question,
                chunks=final_chunks,
                request_body_evidence=request_body_evidence_result,
                requester=requester,
                customer_id=customer_id,
            )
            if rescue_answer is not None:
                return RagQueryResult(
                    answer=rescue_answer,
                    trace=_trace_for(
                        rescue_answer,
                        needs_human=False,
                        handoff_reason=None,
                        generation_mode="request_body_evidence_rescue",
                        extractive_fallback_used=False,
                    ),
                )
            if grounded_overlap:
                generation_mode = "extractive_fallback"
                extractive_fallback_used = True
                answer = _build_extractive_rag_answer(final_chunks)
                return RagQueryResult(
                    answer=answer,
                    trace=_trace_for(
                        answer,
                        needs_human=True,
                        handoff_reason="insufficient_evidence",
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
        answer_body = _supplement_request_body_json_if_missing(
            str(payload["answer"]),
            final_chunks,
            request_body_evidence_result,
        )
        answer_body, citations = _supplement_how_to_code_example_if_missing(
            answer_body,
            question=effective_question,
            chunks=final_chunks,
            citation_ids=citations,
        )
        citation_records = _citation_records_from_ids(citations, final_chunks)
        sources = [
            record.get("source_url") or f"rag:{record['chunk_id']}"
            for record in citation_records
        ]
        answer = RagAnswer(
            answer=_build_answer_text(
                answer_body,
                payload.get("key_steps", []),
                question=effective_question,
                requester=requester,
                customer_id=customer_id,
            ),
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
    rescue_answer = _build_request_body_evidence_rescue_answer(
        question=effective_question,
        chunks=final_chunks,
        request_body_evidence=request_body_evidence_result,
        requester=requester,
        customer_id=customer_id,
    )
    if rescue_answer is not None:
        return RagQueryResult(
            answer=rescue_answer,
            trace=_trace_for(
                rescue_answer,
                needs_human=False,
                handoff_reason=None,
                generation_mode="request_body_evidence_rescue",
                extractive_fallback_used=False,
            ),
        )
    answer = _build_extractive_rag_answer(final_chunks)
    return RagQueryResult(
        answer=answer,
        trace=_trace_for(
            answer,
            needs_human=True,
            handoff_reason="insufficient_evidence",
            generation_mode=generation_mode,
            extractive_fallback_used=extractive_fallback_used,
        ),
    )


def _fanout_child_payload(index: int, query: str, result: RagQueryResult) -> dict[str, Any]:
    return {
        "index": index,
        "query": str(query or "").strip(),
        "query_class": getattr(result.trace, "query_class", None),
        "resolved": not bool(getattr(result.trace, "needs_human", False)),
        "latency_ms": float(getattr(result.trace, "total_latency_ms", 0.0) or 0.0),
        "retrieval_latency_ms": float(getattr(result.trace, "retrieval_latency_ms", 0.0) or 0.0),
        "generation_latency_ms": float(getattr(result.trace, "generation_latency_ms", 0.0) or 0.0),
        "selected_chunk_ids": list(getattr(result.trace, "selected_chunk_ids", []) or []),
        "selected_doc_count": int(getattr(result.trace, "selected_doc_count", 0) or 0),
        "handoff_reason": getattr(result.trace, "handoff_reason", None),
    }


def _coerce_trace_like(base_trace: Any, **overrides: Any) -> RagQueryTrace:
    if is_dataclass(base_trace):
        return replace(base_trace, **overrides)
    values: dict[str, Any] = {}
    for field_info in fields(RagQueryTrace):
        values[field_info.name] = getattr(base_trace, field_info.name, None)
    values.update(overrides)
    return RagQueryTrace(**values)


def _aggregate_fanout_results(
    *,
    message: str,
    child_queries: list[str],
    child_results: list[RagQueryResult],
    total_started_at: float,
) -> RagQueryResult:
    if not child_results:
        raise ValueError("fanout aggregation requires at least one child result")

    aggregated_sources: list[str] = []
    aggregated_citations: list[dict[str, str]] = []
    citation_keys: set[tuple[str, str]] = set()
    retrieved_chunk_ids: list[str] = []
    selected_chunk_ids: list[str] = []
    cited_chunk_ids: list[str] = []
    retrieval_tool_timings: list[dict[str, Any]] = []
    shadow_tools_skipped: list[str] = []
    anchor_hits: list[str] = []
    fanout_children: list[dict[str, Any]] = []
    any_needs_human = False
    handoff_reason: str | None = None
    timeout_stage: str | None = None
    deadline_exhausted = False

    for index, (child_query, child_result) in enumerate(zip(child_queries, child_results, strict=False), start=1):
        fanout_children.append(_fanout_child_payload(index, child_query, child_result))
        trace = child_result.trace
        any_needs_human = any_needs_human or bool(trace.needs_human)
        if handoff_reason is None and trace.handoff_reason:
            handoff_reason = trace.handoff_reason
        deadline_exhausted = deadline_exhausted or bool(getattr(trace, "deadline_exhausted", False))
        if timeout_stage is None and getattr(trace, "timeout_stage", None):
            timeout_stage = trace.timeout_stage
        for source in child_result.answer.sources:
            normalized = str(source or "").strip()
            if normalized and normalized not in aggregated_sources:
                aggregated_sources.append(normalized)
        for citation in child_result.answer.citations:
            if not isinstance(citation, dict):
                continue
            citation_key = (str(citation.get("chunk_id") or "").strip(), str(citation.get("source_url") or "").strip())
            if citation_key in citation_keys:
                continue
            citation_keys.add(citation_key)
            aggregated_citations.append(dict(citation))
        for chunk_id in getattr(trace, "retrieved_chunk_ids", []) or []:
            normalized = str(chunk_id or "").strip()
            if normalized and normalized not in retrieved_chunk_ids:
                retrieved_chunk_ids.append(normalized)
        for chunk_id in getattr(trace, "selected_chunk_ids", []) or []:
            normalized = str(chunk_id or "").strip()
            if normalized and normalized not in selected_chunk_ids:
                selected_chunk_ids.append(normalized)
        for chunk_id in getattr(trace, "cited_chunk_ids", []) or []:
            normalized = str(chunk_id or "").strip()
            if normalized and normalized not in cited_chunk_ids:
                cited_chunk_ids.append(normalized)
        for item in getattr(trace, "retrieval_tool_timings", []) or []:
            if isinstance(item, dict):
                retrieval_tool_timings.append(dict(item))
        for tool_name in getattr(trace, "shadow_tools_skipped", []) or []:
            normalized = str(tool_name or "").strip()
            if normalized and normalized not in shadow_tools_skipped:
                shadow_tools_skipped.append(normalized)
        for hit in getattr(trace, "anchor_hits", []) or []:
            normalized = str(hit or "").strip()
            if normalized and normalized not in anchor_hits:
                anchor_hits.append(normalized)

    if any_needs_human:
        answer_text = INSUFFICIENT_EVIDENCE_REPLY
        answer_sources: list[str] = []
        answer_citations: list[dict[str, str]] = []
        confidence = min(float(result.answer.confidence or 0.55) for result in child_results)
    else:
        answer_text = "\n\n".join(
            f"{index}. {result.answer.answer.strip()}"
            for index, result in enumerate(child_results, start=1)
            if str(result.answer.answer or "").strip()
        ).strip()
        answer_sources = aggregated_sources
        answer_citations = aggregated_citations
        confidence = min(float(result.answer.confidence or 0.0) for result in child_results)

    answer = RagAnswer(
        answer=answer_text,
        confidence=confidence,
        sources=answer_sources,
        citations=answer_citations,
    )
    base_trace = child_results[0].trace
    aggregated_trace = _coerce_trace_like(
        base_trace,
        query_class="api_semantics_mismatch",
        query_type="knowledge_qa",
        retrieval_strategy="agentic_multi_question_fanout_v1",
        vector_candidates_count=sum(int(result.trace.vector_candidates_count or 0) for result in child_results),
        bm25_candidates_count=sum(int(result.trace.bm25_candidates_count or 0) for result in child_results),
        reranked_candidates_count=sum(int(result.trace.reranked_candidates_count or 0) for result in child_results),
        retrieved_chunk_ids=retrieved_chunk_ids,
        selected_chunk_ids=selected_chunk_ids,
        vector_retrieval_latency_ms=round(
            sum(float(result.trace.vector_retrieval_latency_ms or 0.0) for result in child_results),
            2,
        ),
        bm25_retrieval_latency_ms=round(
            sum(float(result.trace.bm25_retrieval_latency_ms or 0.0) for result in child_results),
            2,
        ),
        retrieval_latency_ms=round(
            sum(float(result.trace.retrieval_latency_ms or 0.0) for result in child_results),
            2,
        ),
        rerank_latency_ms=round(
            sum(float(result.trace.rerank_latency_ms or 0.0) for result in child_results),
            2,
        ),
        generation_latency_ms=round(
            sum(float(result.trace.generation_latency_ms or 0.0) for result in child_results),
            2,
        ),
        total_latency_ms=round((time.perf_counter() - total_started_at) * 1000, 2),
        prompt_tokens=sum(int(result.trace.prompt_tokens or 0) for result in child_results),
        completion_tokens=sum(int(result.trace.completion_tokens or 0) for result in child_results),
        embedding_tokens=sum(int(result.trace.embedding_tokens or 0) for result in child_results),
        answer_length=len(answer.answer.strip()),
        citation_count=len(answer.citations),
        cited_chunk_ids=cited_chunk_ids,
        needs_human=any_needs_human,
        handoff_reason=handoff_reason,
        confidence_score=confidence,
        selected_doc_count=len(selected_chunk_ids),
        first_pass_candidate_count=sum(int(result.trace.first_pass_candidate_count or 0) for result in child_results),
        second_pass_candidate_count=sum(int(result.trace.second_pass_candidate_count or 0) for result in child_results),
        first_pass_tools=["fanout_children"],
        plan_query_variants=[{"kind": "fanout_child", "query": query} for query in child_queries if str(query or "").strip()],
        plan_decomposition_targets=[],
        evidence_goal="api_semantics_grounding",
        recovery_bias="lexical",
        judge_summary={
            "decision": "answer_now" if not any_needs_human else "needs_human",
            "reason": "all_children_resolved" if not any_needs_human else (handoff_reason or "child_unresolved"),
        },
        agent_iterations=[],
        primary_shadow_mix={
            "primary": sum(int((result.trace.primary_shadow_mix or {}).get("primary") or 0) for result in child_results),
            "shadow": sum(int((result.trace.primary_shadow_mix or {}).get("shadow") or 0) for result in child_results),
        },
        retrieval_tool_timings=retrieval_tool_timings,
        shadow_tools_skipped=shadow_tools_skipped,
        fanout_used=True,
        fanout_child_count=len(child_results),
        fanout_children=fanout_children,
        deadline_exhausted=deadline_exhausted,
        anchor_hits=anchor_hits,
        timeout_stage=timeout_stage,
    )
    return RagQueryResult(answer=answer, trace=aggregated_trace)


def _run_rag_query_agentic(
    message: str,
    top_k: int | None = None,
    *,
    ticket_context: list[dict[str, str]] | None = None,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    requester: str | None = None,
    product: str | None = None,
    query_policy: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> RagQueryResult | None:
    total_started_at = time.perf_counter()
    fanout_queries = (
        extract_numbered_subqueries(message, max_items=_API_SEMANTICS_MAX_FANOUT_CHILDREN)
        if _fanout_enabled() and is_api_semantics_mismatch_message(message)
        else []
    )
    if len(fanout_queries) < 2:
        return _run_rag_query_agentic_single(
            message,
            top_k=top_k,
            ticket_context=ticket_context,
            ticket_id=ticket_id,
            customer_id=customer_id,
            requester=requester,
            product=product,
            query_policy=query_policy,
            should_cancel=should_cancel,
            record_cancel_stage=record_cancel_stage,
        )

    deadline_at = time.perf_counter() + _api_semantics_total_deadline_seconds()
    retrieval_deadline_seconds = _api_semantics_retrieval_deadline_seconds()
    generation_deadline_seconds = _api_semantics_generation_deadline_seconds()

    def _run_child(child_query: str) -> RagQueryResult:
        child_started_at = time.perf_counter()
        stage_state: dict[str, Any] = {"stage": None, "answer_generation_started_at": None}

        def _child_record(stage: str) -> None:
            normalized_stage = str(stage or "").strip() or None
            stage_state["stage"] = normalized_stage
            if normalized_stage == "answer_generation" and stage_state["answer_generation_started_at"] is None:
                stage_state["answer_generation_started_at"] = time.perf_counter()
            if callable(record_cancel_stage) and normalized_stage:
                record_cancel_stage(normalized_stage)

        def _child_should_cancel() -> bool:
            if callable(should_cancel) and should_cancel():
                return True
            now_value = time.perf_counter()
            if now_value >= deadline_at:
                stage_state["deadline_exhausted"] = True
                stage_state["timeout_stage"] = stage_state.get("stage") or "total_deadline"
                return True
            answer_started_at = stage_state.get("answer_generation_started_at")
            if answer_started_at is not None and (now_value - float(answer_started_at)) >= generation_deadline_seconds:
                stage_state["deadline_exhausted"] = True
                stage_state["timeout_stage"] = "answer_generation"
                return True
            if answer_started_at is None and stage_state.get("stage") and (now_value - child_started_at) >= retrieval_deadline_seconds:
                stage_state["deadline_exhausted"] = True
                stage_state["timeout_stage"] = stage_state.get("stage") or "retrieval"
                return True
            return False

        result = _run_rag_query_agentic_single(
            child_query,
            top_k=top_k,
            ticket_context=ticket_context,
            ticket_id=ticket_id,
            customer_id=customer_id,
            requester=requester,
            product=product,
            query_policy=query_policy,
            should_cancel=_child_should_cancel,
            record_cancel_stage=_child_record,
        )
        if result is not None and bool(stage_state.get("deadline_exhausted")):
            result.trace.deadline_exhausted = True
            result.trace.timeout_stage = str(stage_state.get("timeout_stage") or "").strip() or None
        return result

    child_results: list[RagQueryResult] = []
    with ThreadPoolExecutor(max_workers=min(2, len(fanout_queries))) as executor:
        futures = [executor.submit(_run_child, child_query) for child_query in fanout_queries]
        for future in futures:
            try:
                child_result = future.result()
            except RagExecutionCancelled as exc:
                child_result = RagQueryResult(
                    answer=RagAnswer(
                        answer=INSUFFICIENT_EVIDENCE_REPLY,
                        confidence=0.55,
                        sources=[],
                        citations=[],
                    ),
                    trace=RagQueryTrace(
                        query_type="knowledge_qa",
                        retrieval_strategy="agentic_multi_tool_v1",
                        vector_candidates_count=0,
                        bm25_candidates_count=0,
                        reranked_candidates_count=0,
                        retrieved_chunk_ids=[],
                        selected_chunk_ids=[],
                        vector_retrieval_latency_ms=0.0,
                        bm25_retrieval_latency_ms=0.0,
                        retrieval_latency_ms=0.0,
                        rerank_latency_ms=0.0,
                        generation_latency_ms=0.0,
                        total_latency_ms=round((time.perf_counter() - total_started_at) * 1000, 2),
                        prompt_tokens=0,
                        completion_tokens=0,
                        embedding_tokens=0,
                        embedding_provider=None,
                        embedding_model=None,
                        embedding_dimensions=None,
                        embedding_request_meta=[],
                        model_name=None,
                        answer_length=len(INSUFFICIENT_EVIDENCE_REPLY),
                        citation_count=0,
                        cited_chunk_ids=[],
                        needs_human=True,
                        handoff_reason="deadline_exhausted",
                        confidence_score=0.55,
                        primary_source_type=None,
                        primary_chunk_strategy=None,
                        query_class="api_semantics_mismatch",
                        evidence_goal="api_semantics_grounding",
                        recovery_bias="lexical",
                        execution_mode="agentic",
                        shadow_retrieval_enabled=_shadow_retrieval_enabled(),
                        fanout_used=False,
                        deadline_exhausted=True,
                        anchor_hits=extract_anchor_hits(message),
                        timeout_stage=exc.stage,
                    ),
                )
            if child_result is not None:
                child_results.append(child_result)

    if not child_results:
        return None
    return _aggregate_fanout_results(
        message=message,
        child_queries=fanout_queries[: len(child_results)],
        child_results=child_results,
        total_started_at=total_started_at,
    )


def run_rag_query(
    message: str,
    top_k: int | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    requester: str | None = None,
    product: str | None = None,
    query_policy: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_cancel_stage: Callable[[str], None] | None = None,
) -> RagQueryResult | None:
    if not _feature_flag_enabled("RAG_AGENT_ENABLED", True):
        result = _run_rag_query_legacy(
            message,
            top_k=top_k,
            ticket_context=ticket_context,
            requester=requester,
            customer_id=customer_id,
            product=product,
            query_policy=query_policy,
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
            requester=requester,
            product=product,
            query_policy=query_policy,
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
            ticket_context=ticket_context,
            requester=requester,
            customer_id=customer_id,
            product=product,
            query_policy=query_policy,
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

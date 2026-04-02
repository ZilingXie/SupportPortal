from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any

from backend.services.embedding_provider import (
    DEFAULT_PGVECTOR_TABLE,
    embedding_model_id,
    embedding_provider_name,
    get_embedding_provider,
)
from backend.services.prompts.rag_answer import build_rag_answer_system_prompt, build_rag_answer_user_prompt
from backend.services.query_understanding import QueryUnderstandingResult, RetrievalPlan, understand_rag_query
from backend.services.rag_tokenizer import is_bm25_query_stopword, tokenize_bm25_query

logger = logging.getLogger(__name__)
_UNAVAILABLE_MODELS: set[str] = set()
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

SYSTEM_PROMPT = build_rag_answer_system_prompt(insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY)


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    source_path: str
    similarity: float
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


@dataclass
class RagQueryResult:
    answer: RagAnswer
    trace: RagQueryTrace


def _feature_flag_enabled(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


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
    if rewrite_enabled:
        for query in understanding.rewritten_queries:
            variants.append(("rewrite", str(query).strip()))
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
        }
    if understanding is None:
        return {
            "query_profile": "",
            "glossary_hit_terms": [],
            "applied_hard_filters": {},
            "applied_soft_signals": {},
            "fallback_mode": "disabled",
        }
    return {
        "query_profile": understanding.query_profile,
        "glossary_hit_terms": list(understanding.canonical_terms),
        "applied_hard_filters": dict(understanding.retrieval_plan.hard_filters),
        "applied_soft_signals": dict(understanding.retrieval_plan.soft_signals),
        "fallback_mode": understanding.fallback_mode,
    }


def _drain_embedding_request_meta(provider: Any) -> list[dict[str, Any]]:
    try:
        raw_items = provider.drain_request_log()
    except Exception:
        return []
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _import_langchain() -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI


def _import_psycopg() -> Any:
    import psycopg

    return psycopg


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.10f}" for v in values) + "]"


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
        },
    }


def _get_rag_config(top_k: int | None = None) -> dict[str, Any]:
    dsn = (os.getenv("PGVECTOR_DSN") or "").strip()
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    final_top_k = max(1, int(top_k)) if top_k is not None else _safe_int_env("RAG_TOP_K", 6)
    vector_candidate_k = _safe_int_env("RAG_VECTOR_CANDIDATE_K", max(40, final_top_k * 10))
    bm25_candidate_k = _safe_int_env("RAG_BM25_CANDIDATE_K", max(40, final_top_k * 10))
    fusion_candidate_k = _safe_int_env("RAG_FUSION_CANDIDATE_K", max(30, final_top_k * 8))
    rerank_top_n = _safe_int_env("RAG_RERANK_TOP_N", max(20, final_top_k * 4))
    schema = (os.getenv("PGVECTOR_SCHEMA") or "supportportal").strip() or "supportportal"
    raw_table = (os.getenv("PGVECTOR_TABLE") or DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE
    table_name = raw_table if "." in raw_table else f"{schema}.{raw_table}"
    return {
        "dsn": dsn,
        "api_key": api_key,
        "app_schema": schema,
        "table": table_name,
        "top_k": final_top_k,
        "vector_candidate_k": vector_candidate_k,
        "bm25_candidate_k": bm25_candidate_k,
        "keyword_candidate_k": bm25_candidate_k,
        "fusion_candidate_k": fusion_candidate_k,
        "rerank_top_n": rerank_top_n,
        "bm25_k1": _safe_float_env("RAG_BM25_K1", 1.2),
        "bm25_b": _safe_float_env("RAG_BM25_B", 0.75),
        "bm25_max_query_terms": _safe_int_env("RAG_BM25_MAX_QUERY_TERMS", 6),
        "bm25_max_term_doc_freq_ratio": _safe_float_env("RAG_BM25_MAX_TERM_DOC_FREQ_RATIO", 0.08),
        "chat_model": (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4.1").strip(),
        "embedding_provider": embedding_provider_name(),
        "embedding_model": embedding_model_id(),
        "rerank_provider": (os.getenv("RAG_RERANK_PROVIDER") or "siliconflow").strip() or "siliconflow",
        "rerank_model": (os.getenv("RAG_RERANK_MODEL") or "BAAI/bge-reranker-v2-m3").strip() or "BAAI/bge-reranker-v2-m3",
        "rerank_api_key": (
            (os.getenv("RAG_RERANK_API_KEY") or "").strip()
            or (os.getenv("SILICONFLOW_API_KEY") or "").strip()
            or (os.getenv("SILICONFLOW_KEY") or "").strip()
            or (os.getenv("SILLICONFLOW_KEY") or "").strip()
            or (os.getenv("siliconflow_key") or "").strip()
            or (os.getenv("silliconflow_key") or "").strip()
        ),
        "rerank_base_url": (
            (os.getenv("RAG_RERANK_BASE_URL") or "").strip()
            or (os.getenv("SILICONFLOW_BASE_URL") or "https://api.siliconflow.cn/v1").strip()
        ),
        "rerank_timeout_seconds": _safe_float_env("RAG_RERANK_TIMEOUT_SECONDS", 10.0),
        "rerank_max_retries": _safe_int_env("RAG_RERANK_MAX_RETRIES", 1),
        "request_timeout_seconds": _safe_float_env("RAG_REQUEST_TIMEOUT_SECONDS", 20.0),
        "max_retries": _safe_int_env("RAG_OPENAI_MAX_RETRIES", 1),
    }


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
        return fallback_table

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


def _retrieve_chunks(message: str, config: dict[str, Any], *, limit: int | None = None) -> list[RetrievedChunk]:
    psycopg = _import_psycopg()
    sql = psycopg.sql

    provider = get_embedding_provider()
    query_embedding = provider.embed_query(message)
    vector_param = _vector_literal(query_embedding)

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
        WHERE index_role = 'primary'
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """
    ).format(_table_identifier(sql, config["table"]))

    with psycopg.connect(config["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (vector_param, vector_param, int(limit or config["top_k"])))
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
                retrieval_sources=["vector"],
                candidate_trace={"vector_similarity": float(row[11]) if row[11] is not None else 0.0},
            )
        )
    return chunks


def _retrieve_bm25_chunks(message: str, config: dict[str, Any], *, limit: int | None = None) -> list[RetrievedChunk]:
    terms = tokenize_bm25_query(message)
    if not terms:
        return []

    psycopg = _import_psycopg()
    sql = psycopg.sql
    app_schema = str(config.get("app_schema") or "supportportal").strip() or "supportportal"
    query = sql.SQL(
        """
        WITH query_terms AS (
            SELECT
                q.term,
                t.doc_freq
            FROM unnest(%s::text[]) AS q(term)
            JOIN {} AS t
              ON t.term = q.term
             AND t.index_role = 'primary'
        ),
        stats AS (
            SELECT doc_count, avg_doc_length
            FROM {}
            WHERE index_role = 'primary'
        ),
        matched_postings AS MATERIALIZED (
            SELECT
                p.chunk_id,
                p.tf,
                q.doc_freq
            FROM query_terms AS q
            JOIN {} AS p
              ON p.term = q.term
             AND p.index_role = 'primary'
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
            WHERE d.index_role = 'primary'
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
        WHERE v.index_role = 'primary'
        ORDER BY scored.bm25_score DESC, v.updated_at DESC
        LIMIT %s
        """
    ).format(
        _app_table_identifier(sql, app_schema, "support_knowledge_bm25_terms"),
        _app_table_identifier(sql, app_schema, "support_knowledge_bm25_stats"),
        _app_table_identifier(sql, app_schema, "support_knowledge_bm25_postings"),
        _app_table_identifier(sql, app_schema, "support_knowledge_bm25_docs"),
        _table_identifier(sql, config["table"]),
    )

    with psycopg.connect(config["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT term, doc_freq
                    FROM {}
                    WHERE index_role = 'primary'
                      AND term = ANY(%s)
                    """
                ).format(_app_table_identifier(sql, app_schema, "support_knowledge_bm25_terms")),
                (terms,),
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
                    WHERE index_role = 'primary'
                    """
                ).format(_app_table_identifier(sql, app_schema, "support_knowledge_bm25_stats"))
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
                    float(config["bm25_k1"]),
                    float(config["bm25_k1"]),
                    float(config["bm25_b"]),
                    float(config["bm25_b"]),
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
                retrieval_sources=["bm25"],
                candidate_trace={"bm25_score": raw_score},
            )
        )
    return chunks


def _retrieve_fts_chunks(message: str, config: dict[str, Any], *, limit: int | None = None) -> list[RetrievedChunk]:
    psycopg = _import_psycopg()
    sql = psycopg.sql

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
        WHERE index_role = 'primary'
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
            cur.execute(query, (message, message, int(limit or config["keyword_candidate_k"])))
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
) -> list[RetrievedChunk]:
    terms = _extract_query_terms(message)
    if not terms:
        return []

    psycopg = _import_psycopg()
    sql = psycopg.sql
    patterns = [f"%{term}%" for term in terms]
    candidate_limit = max(int(config["top_k"]) * 25, 50)

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
            index_role = 'primary'
            AND (
            lower(content) LIKE ANY(%s)
            OR lower(coalesce(h1, '')) LIKE ANY(%s)
            OR lower(coalesce(h2, '')) LIKE ANY(%s)
            OR lower(coalesce(h3, '')) LIKE ANY(%s)
            )
        LIMIT %s
        """
    ).format(_table_identifier(sql, config["table"]))

    with psycopg.connect(config["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (patterns, patterns, patterns, patterns, candidate_limit))
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
        )
        hits = _keyword_hit_count(_chunk_search_text(chunk), terms)
        if hits <= 0:
            continue
        chunk.similarity = min(1.0, hits / max(1, len(terms)))
        chunk.retrieval_sources = ["keyword_fallback"]
        chunk.candidate_trace = {
            "keyword_fallback_hits": hits,
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


def _invoke_llm_payload(
    message: str,
    chunks: list[RetrievedChunk],
    config: dict[str, Any],
    strict_retry: bool = False,
) -> dict[str, Any] | None:
    ChatOpenAI = _import_langchain()
    context_block = _format_context(chunks)
    prompt = _build_answer_prompt_for_mode(message, context_block, repair_mode=strict_retry)

    model_candidates: list[str] = []
    for candidate in [config["chat_model"], "gpt-4.1", "gpt-4o-mini"]:
        if candidate in _UNAVAILABLE_MODELS:
            continue
        if candidate not in model_candidates:
            model_candidates.append(candidate)

    for model_name in model_candidates:
        try:
            llm = ChatOpenAI(
                model=model_name,
                temperature=0,
                api_key=config["api_key"],
                request_timeout=config["request_timeout_seconds"],
                max_retries=int(config["max_retries"]),
            )
            response = llm.invoke([("system", SYSTEM_PROMPT), ("user", prompt)])
            payload = _extract_json_payload(_response_to_text(response))
            if payload is not None:
                return payload
        except Exception as exc:
            lower = str(exc).lower()
            if "model_not_found" in lower or "does not exist" in lower:
                _UNAVAILABLE_MODELS.add(model_name)
                logger.warning("RAG model unavailable (%s), trying fallback model", model_name)
                continue
            raise
    return None


def _invoke_llm_payload_with_trace(
    message: str,
    chunks: list[RetrievedChunk],
    config: dict[str, Any],
    strict_retry: bool = False,
) -> tuple[dict[str, Any] | None, int, int, str | None]:
    ChatOpenAI = _import_langchain()
    context_block = _format_context(chunks)
    prompt = _build_answer_prompt_for_mode(message, context_block, repair_mode=strict_retry)

    model_candidates: list[str] = []
    for candidate in [config["chat_model"], "gpt-4.1", "gpt-4o-mini"]:
        if candidate in _UNAVAILABLE_MODELS:
            continue
        if candidate not in model_candidates:
            model_candidates.append(candidate)

    for model_name in model_candidates:
        try:
            llm = ChatOpenAI(
                model=model_name,
                temperature=0,
                api_key=config["api_key"],
                request_timeout=config["request_timeout_seconds"],
                max_retries=int(config["max_retries"]),
            )
            response = llm.invoke([("system", SYSTEM_PROMPT), ("user", prompt)])
            payload = _extract_json_payload(_response_to_text(response))
            prompt_tokens, completion_tokens = _usage_tokens_from_response(response)
            if payload is not None:
                return payload, prompt_tokens, completion_tokens, model_name
        except Exception as exc:
            lower = str(exc).lower()
            if "model_not_found" in lower or "does not exist" in lower:
                _UNAVAILABLE_MODELS.add(model_name)
                logger.warning("RAG model unavailable (%s), trying fallback model", model_name)
                continue
            raise
    return None, 0, 0, None


def _confidence_from_chunks(chunks: list[RetrievedChunk]) -> float:
    if not chunks:
        return 0.0
    best_similarity = max(0.0, min(1.0, chunks[0].similarity))
    confidence = 0.72 + (0.2 * best_similarity) + (0.02 * min(len(chunks), 5))
    return round(min(0.95, confidence), 2)


def _usage_tokens_from_response(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, dict):
        token_usage = response_metadata.get("token_usage") if isinstance(response_metadata.get("token_usage"), dict) else {}
        return int(token_usage.get("prompt_tokens") or 0), int(token_usage.get("completion_tokens") or 0)
    return 0, 0


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
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=float(config.get("rerank_timeout_seconds") or 10.0)) as response:
                raw_payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.warning("RAG rerank request failed attempt=%s error=%s", attempt + 1, exc)
            raw_payload = None
    if raw_payload is None:
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


def run_rag_query(message: str, top_k: int | None = None) -> RagQueryResult | None:
    config = _get_rag_config(top_k=top_k)
    resolved_table = _resolve_active_vector_table(config)
    if resolved_table:
        config["table"] = resolved_table
    if not config["dsn"] or not config["api_key"]:
        return None

    provider = get_embedding_provider()
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
    query_understanding: QueryUnderstandingResult | None = None
    effective_hard_filters: dict[str, str] = {}
    effective_soft_signals: dict[str, list[str]] = {}
    effective_rewrites: list[str] = []
    effective_decomposition_subqueries: list[str] = []
    query_variants: list[tuple[str, str]] = [("original", str(message or "").strip())]
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
    if query_understanding_enabled:
        try:
            query_understanding = understand_rag_query(message)
        except Exception as exc:
            logger.warning("RAG query understanding failed: %s", exc)
    if query_understanding is not None:
        effective_hard_filters = dict(query_understanding.retrieval_plan.hard_filters)
        effective_soft_signals = dict(query_understanding.retrieval_plan.soft_signals)
        effective_rewrites = list(query_understanding.rewritten_queries) if query_rewrite_enabled else []
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
    else:
        effective_plan = RetrievalPlan(semantic_query=str(message or "").strip())

    for query_kind, variant_query in query_variants:
        try:
            vector_started_at = time.perf_counter()
            variant_vector_chunks = _retrieve_chunks(
                variant_query,
                config,
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

        try:
            bm25_started_at = time.perf_counter()
            variant_bm25_chunks = _retrieve_bm25_chunks(
                variant_query,
                config,
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
                    config,
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
            except Exception as keyword_exc:
                logger.warning("RAG keyword retrieval failed for %s query: %s", query_kind, keyword_exc)

    if not vector_chunks and not bm25_chunks:
        for query_kind, variant_query in query_variants:
            try:
                keyword_started_at = time.perf_counter()
                variant_keyword_chunks = _retrieve_keyword_chunks(
                    variant_query,
                    config,
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

    if vector_chunks and not bm25_chunks and keyword_fallback_chunks:
        chunks = _merge_chunks(
            vector_chunks,
            keyword_fallback_chunks,
            limit=int(config["fusion_candidate_k"]),
        )
    elif vector_chunks or bm25_chunks:
        chunks = _rrf_merge(
            vector_chunks,
            bm25_chunks,
            limit=int(config["fusion_candidate_k"]),
        )
        if not chunks:
            chunks = _merge_chunks(
                vector_chunks,
                bm25_chunks,
                limit=int(config["fusion_candidate_k"]),
            )
    elif keyword_fallback_chunks:
        chunks = _merge_chunks(
            vector_chunks,
            keyword_fallback_chunks,
            limit=int(config["fusion_candidate_k"]),
        )

    if not chunks:
        for query_kind, variant_query in query_variants:
            try:
                keyword_started_at = time.perf_counter()
                variant_keyword_chunks = _retrieve_keyword_chunks(variant_query, config, limit=int(config["top_k"]))
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
                rewritten_queries=list(effective_rewrites),
                decomposition_subqueries=list(effective_decomposition_subqueries),
            )
            return RagQueryResult(answer=answer, trace=trace)

    retrieved_chunks = list(chunks)
    if chunks:
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
    allowed_chunk_ids = {chunk.chunk_id for chunk in final_chunks}
    grounded_overlap = _has_grounded_keyword_overlap(message, final_chunks)
    payload: dict[str, Any] | None = None
    try:
        generation_started_at = time.perf_counter()
        payload, prompt_tokens, completion_tokens, model_name = _invoke_llm_payload_with_trace(
            message,
            final_chunks,
            config,
            strict_retry=False,
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
                config,
                strict_retry=True,
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
            selected_contexts=_selected_contexts(final_chunks),
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
            rewritten_queries=list(effective_rewrites),
            decomposition_subqueries=list(effective_decomposition_subqueries),
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


def answer_with_rag(message: str, top_k: int | None = None) -> RagAnswer | None:
    """
    Attempt to answer with PostgreSQL pgvector retrieval + LangChain answer generation.
    Returns None when RAG is not configured or retrieval fails, so caller can fallback.
    """
    result = run_rag_query(message, top_k=top_k)
    return result.answer if result is not None else None

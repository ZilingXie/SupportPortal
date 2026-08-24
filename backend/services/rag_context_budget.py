from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    ModelProfile,
    RAG_CONTEXT_COMPRESSION_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.prompts.rag_context_compression import (
    build_rag_context_compression_system_prompt,
    build_rag_context_compression_user_prompt,
)
from backend.services.prompt_runtime import resolve_system_prompt
from backend.services.token_usage import build_usage_ledger_entry

if TYPE_CHECKING:
    from backend.services.rag_qa import RetrievedChunk

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
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
    "my",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "with",
}
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_DEFAULT_MODEL_CONTEXT_WINDOWS = {
    "gpt-5": 400_000,
    "gpt-5.4": 400_000,
    "gpt-5.4-mini": 400_000,
    "gpt-5.4-nano": 400_000,
    "gpt-4.1": 128_000,
    "gpt-4.1-mini": 128_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
}


@dataclass(frozen=True)
class ContextBudget:
    context_window: int
    system_prompt_tokens: int
    history_tokens: int
    prompt_tokens: int
    tool_tokens: int
    reserved_output_tokens: int
    buffer_tokens: int
    available_context_tokens: int


@dataclass(frozen=True)
class EvidenceSegment:
    chunk_id: str
    source_path: str
    heading: str
    text: str
    source_url: str | None = None
    doc_id: str | None = None
    similarity: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    rerank_score: float | None = None
    rerank_reasons: list[str] = field(default_factory=list)
    packing_mode: str = "extractive"


@dataclass(frozen=True)
class PackedEvidence:
    budget: ContextBudget
    chunk_ids: list[str]
    prompt_context: str
    selected_contexts: list[dict[str, Any]]
    raw_context_token_estimate: int
    packed_context_token_estimate: int
    compression_triggered: bool
    compression_trigger_reason: str | None
    compression_mode: str
    compression_model: str | None
    extractive_segment_count: int
    packed_evidence_count: int
    compression_usage_ledger: list[dict[str, Any]] = field(default_factory=list)


def estimate_text_tokens(text: Any) -> int:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return 0
    word_count = len(normalized.split())
    char_estimate = math.ceil(len(normalized) / 4)
    return max(1, word_count, char_estimate)


def model_context_window(model_name: str | None) -> int:
    normalized = " ".join(str(model_name or "").split()).strip().lower()
    if not normalized:
        return 128_000
    if normalized in _DEFAULT_MODEL_CONTEXT_WINDOWS:
        return _DEFAULT_MODEL_CONTEXT_WINDOWS[normalized]
    if normalized.startswith("gpt-5"):
        return 400_000
    return 128_000


def build_context_budget(
    *,
    context_window: int,
    system_prompt_text: str,
    history_text: str,
    user_prompt_text: str,
    tool_schema_text: str,
    reserved_output_tokens: int,
    buffer_tokens: int,
) -> ContextBudget:
    safe_window = max(1, int(context_window or 1))
    safe_reserved_output = max(0, int(reserved_output_tokens or 0))
    safe_buffer = max(0, int(buffer_tokens or 0))
    system_prompt_tokens = estimate_text_tokens(system_prompt_text)
    history_tokens = estimate_text_tokens(history_text)
    prompt_tokens = estimate_text_tokens(user_prompt_text)
    tool_tokens = estimate_text_tokens(tool_schema_text)
    available_context_tokens = max(
        0,
        safe_window
        - (
            system_prompt_tokens
            + history_tokens
            + prompt_tokens
            + tool_tokens
            + safe_reserved_output
            + safe_buffer
        ),
    )
    return ContextBudget(
        context_window=safe_window,
        system_prompt_tokens=system_prompt_tokens,
        history_tokens=history_tokens,
        prompt_tokens=prompt_tokens,
        tool_tokens=tool_tokens,
        reserved_output_tokens=safe_reserved_output,
        buffer_tokens=safe_buffer,
        available_context_tokens=available_context_tokens,
    )


def _clean_text(text: Any) -> str:
    return " ".join(str(text or "").split()).strip()


def _build_heading(chunk: RetrievedChunk) -> str:
    heading_items = [item for item in [getattr(chunk, "h1", None), getattr(chunk, "h2", None), getattr(chunk, "h3", None)] if item]
    return " > ".join(heading_items) if heading_items else "Unknown heading"


def _context_entry_from_chunk(
    chunk: RetrievedChunk,
    text: str,
    *,
    packing_mode: str,
) -> dict[str, Any]:
    return {
        "chunk_id": getattr(chunk, "chunk_id", ""),
        "doc_id": getattr(chunk, "doc_id", None),
        "source_path": getattr(chunk, "source_path", ""),
        "heading": _build_heading(chunk),
        "source_url": getattr(chunk, "source_url", None),
        "source_type": getattr(chunk, "source_type", None),
        "chunk_strategy": getattr(chunk, "chunk_strategy", None),
        "similarity": round(max(0.0, min(1.0, float(getattr(chunk, "similarity", 0.0) or 0.0))), 4),
        "metadata": dict(getattr(chunk, "metadata", {}) or {}),
        "rerank_score": getattr(chunk, "rerank_score", None),
        "rerank_reasons": list(getattr(chunk, "rerank_reasons", []) or []),
        "text": _clean_text(text),
        "text_excerpt": _clean_text(text),
        "packing_mode": packing_mode,
    }


def _format_context_from_entries(entries: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for entry in entries:
        blocks.append(
            f"[{entry.get('chunk_id')}] {entry.get('source_path')} | {entry.get('heading')}\n"
            f"{_clean_text(entry.get('text'))}"
        )
    return "\n\n---\n\n".join(blocks)


def _truncate_text_to_budget(text: str, *, max_tokens: int) -> str:
    normalized = _clean_text(text)
    if not normalized:
        return ""
    safe_budget = max(1, int(max_tokens or 1))
    if estimate_text_tokens(normalized) <= safe_budget:
        return normalized
    words = normalized.split()
    if not words:
        return normalized
    kept: list[str] = []
    for word in words:
        candidate = " ".join([*kept, word]).strip()
        if estimate_text_tokens(candidate) > safe_budget:
            break
        kept.append(word)
    if kept:
        return " ".join(kept).strip()
    rough_chars = max(12, safe_budget * 4)
    return normalized[:rough_chars].rstrip() + "..."


def _fit_entries_to_budget(entries: list[dict[str, Any]], available_tokens: int) -> list[dict[str, Any]]:
    safe_budget = max(1, int(available_tokens or 1))
    selected: list[dict[str, Any]] = []
    used_tokens = 0
    for entry in entries:
        formatted = _format_context_from_entries([entry])
        entry_tokens = estimate_text_tokens(formatted)
        separator_tokens = 0 if not selected else estimate_text_tokens("---")
        if used_tokens + separator_tokens + entry_tokens <= safe_budget:
            selected.append(entry)
            used_tokens += separator_tokens + entry_tokens
            continue
        if selected:
            continue
        header = f"[{entry.get('chunk_id')}] {entry.get('source_path')} | {entry.get('heading')}"
        header_tokens = estimate_text_tokens(header)
        remaining_tokens = max(1, safe_budget - header_tokens)
        truncated = dict(entry)
        truncated_text = _truncate_text_to_budget(_clean_text(entry.get("text")), max_tokens=remaining_tokens)
        truncated["text"] = truncated_text
        truncated["text_excerpt"] = truncated_text
        selected.append(truncated)
        break
    return selected


def _split_sentences(text: str) -> list[str]:
    normalized = _clean_text(text)
    if not normalized:
        return []
    segments = [segment.strip() for segment in _SENTENCE_SPLIT_RE.split(normalized) if segment.strip()]
    return segments or [normalized]


def _query_terms(question: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[a-zA-Z0-9_.+-]+", str(question or "").lower()):
        normalized = token.strip().lower()
        if not normalized or normalized in _STOPWORDS or len(normalized) < 3:
            continue
        terms.add(normalized)
    return terms


def _score_sentence(question_terms: set[str], heading: str, sentence: str) -> tuple[int, int, int]:
    lowered_sentence = sentence.lower()
    lowered_heading = heading.lower()
    query_hits = sum(1 for term in question_terms if term in lowered_sentence)
    heading_hits = sum(1 for term in question_terms if term in lowered_heading)
    return query_hits, heading_hits, -len(sentence)


def _extractive_text(question: str, chunk: RetrievedChunk, *, max_sentences: int = 2) -> str:
    sentences = _split_sentences(getattr(chunk, "text", ""))
    if not sentences:
        return ""
    heading = _build_heading(chunk)
    terms = _query_terms(question)
    if not terms:
        return " ".join(sentences[:max(1, int(max_sentences))]).strip()
    ranked = sorted(
        sentences,
        key=lambda sentence: _score_sentence(terms, heading, sentence),
        reverse=True,
    )
    selected: list[str] = []
    seen: set[str] = set()
    for sentence in ranked:
        normalized = _clean_text(sentence)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        selected.append(normalized)
        if len(selected) >= max(1, int(max_sentences)):
            break
    if not selected:
        selected = [_clean_text(sentences[0])]
    return " ".join(selected).strip()


def _redundancy_ratio(chunks: list[RetrievedChunk]) -> float:
    if not chunks:
        return 0.0
    signatures = {
        (
            _clean_text(getattr(chunk, "source_path", "")),
            _build_heading(chunk),
        )
        for chunk in chunks
    }
    return 1.0 - (len(signatures) / max(1, len(chunks)))


def _compression_trigger_reason(
    *,
    raw_context_token_estimate: int,
    candidate_count: int,
    redundancy_ratio: float,
    budget: ContextBudget,
    top_k: int | None,
) -> str | None:
    if raw_context_token_estimate > budget.available_context_tokens:
        return "token_budget"
    if candidate_count > max(6, int(top_k or 0) * 2):
        return "candidate_overflow"
    if redundancy_ratio >= 0.4:
        return "redundancy"
    return None


def _compression_entries_payload(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for entry in entries:
        payload.append(
            {
                "chunk_id": entry.get("chunk_id"),
                "source_path": entry.get("source_path"),
                "heading": entry.get("heading"),
                "snippet": entry.get("text"),
            }
        )
    return payload


def _compress_entries_with_llm(
    *,
    question: str,
    entries: list[dict[str, Any]],
    available_context_tokens: int,
    compression_profile: ModelProfile,
) -> tuple[list[dict[str, Any]] | None, str | None, dict[str, Any] | None]:
    if not entries or not profile_has_invocation_credentials(compression_profile):
        return None, None, None
    try:
        response = invoke_responses_text(
            profile=compression_profile,
            system_prompt=resolve_system_prompt("rag-context-compression", build_rag_context_compression_system_prompt()),
            user_prompt=build_rag_context_compression_user_prompt(
                question=question,
                evidence_segments=_compression_entries_payload(entries),
                available_context_tokens=available_context_tokens,
            ),
        )
    except LlmInvocationError:
        return None, None, None
    usage_entry = build_usage_ledger_entry(
        provider=compression_profile.provider,
        model=compression_profile.model,
        stage="context_compression",
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        input_tokens=response.prompt_tokens,
        output_tokens=response.completion_tokens,
        cached_input_tokens=response.cached_input_tokens,
        reasoning_tokens=response.reasoning_tokens,
    )
    try:
        parsed = json.loads(str(response.text or "").strip())
    except json.JSONDecodeError:
        return None, response.model_name, usage_entry
    items = parsed.get("evidence") if isinstance(parsed, dict) and isinstance(parsed.get("evidence"), list) else []
    entry_map = {str(entry.get("chunk_id") or "").strip(): entry for entry in entries}
    compressed_entries: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        chunk_id = _clean_text(item.get("chunk_id"))
        packed_text = _clean_text(item.get("packed_text"))
        base_entry = entry_map.get(chunk_id)
        if not chunk_id or not packed_text or base_entry is None:
            continue
        compressed_entry = dict(base_entry)
        compressed_entry["text"] = packed_text
        compressed_entry["text_excerpt"] = packed_text
        compressed_entry["packing_mode"] = "compressive"
        compressed_entries.append(compressed_entry)
    if not compressed_entries:
        return None, response.model_name, usage_entry
    return compressed_entries, response.model_name, usage_entry


def build_packed_evidence(
    *,
    question: str,
    chunks: list[RetrievedChunk],
    system_prompt_text: str,
    user_prompt_text: str,
    tool_schema_text: str,
    context_window: int,
    reserved_output_tokens: int,
    buffer_tokens: int,
    compression_enabled: bool,
    compression_profile: ModelProfile | None,
    history_text: str = "",
    top_k: int | None = None,
) -> PackedEvidence:
    normalized_chunks = [chunk for chunk in chunks if _clean_text(getattr(chunk, "text", ""))]
    budget = build_context_budget(
        context_window=context_window,
        system_prompt_text=system_prompt_text,
        history_text=history_text,
        user_prompt_text=user_prompt_text,
        tool_schema_text=tool_schema_text,
        reserved_output_tokens=reserved_output_tokens,
        buffer_tokens=buffer_tokens,
    )
    raw_entries = [_context_entry_from_chunk(chunk, getattr(chunk, "text", ""), packing_mode="raw") for chunk in normalized_chunks]
    raw_context = _format_context_from_entries(raw_entries)
    raw_context_token_estimate = estimate_text_tokens(raw_context)
    trigger_reason = _compression_trigger_reason(
        raw_context_token_estimate=raw_context_token_estimate,
        candidate_count=len(normalized_chunks),
        redundancy_ratio=_redundancy_ratio(normalized_chunks),
        budget=budget,
        top_k=top_k,
    )
    if not normalized_chunks:
        return PackedEvidence(
            budget=budget,
            chunk_ids=[],
            prompt_context="",
            selected_contexts=[],
            raw_context_token_estimate=0,
            packed_context_token_estimate=0,
            compression_triggered=False,
            compression_trigger_reason=None,
            compression_mode="raw",
            compression_model=None,
            extractive_segment_count=0,
            packed_evidence_count=0,
            compression_usage_ledger=[],
        )

    if trigger_reason is None or not compression_enabled:
        selected_entries = _fit_entries_to_budget(raw_entries, budget.available_context_tokens)
        prompt_context = _format_context_from_entries(selected_entries)
        return PackedEvidence(
            budget=budget,
            chunk_ids=[str(entry.get("chunk_id") or "").strip() for entry in selected_entries if str(entry.get("chunk_id") or "").strip()],
            prompt_context=prompt_context,
            selected_contexts=selected_entries,
            raw_context_token_estimate=raw_context_token_estimate,
            packed_context_token_estimate=estimate_text_tokens(prompt_context),
            compression_triggered=False,
            compression_trigger_reason=None,
            compression_mode="raw",
            compression_model=None,
            extractive_segment_count=0,
            packed_evidence_count=len(selected_entries),
            compression_usage_ledger=[],
        )

    extractive_entries: list[dict[str, Any]] = []
    for chunk in normalized_chunks:
        extractive_text = _extractive_text(question, chunk)
        if not extractive_text:
            continue
        extractive_entries.append(_context_entry_from_chunk(chunk, extractive_text, packing_mode="extractive"))
    if not extractive_entries:
        extractive_entries = _fit_entries_to_budget(raw_entries, budget.available_context_tokens)

    compression_model: str | None = None
    compression_mode = "extractive"
    packed_entries = _fit_entries_to_budget(extractive_entries, budget.available_context_tokens)
    compression_usage_ledger: list[dict[str, Any]] = []
    if compression_enabled:
        profile = compression_profile or resolve_model_profile(RAG_CONTEXT_COMPRESSION_SCENARIO)
        compressed_entries, compression_model, usage_entry = _compress_entries_with_llm(
            question=question,
            entries=extractive_entries,
            available_context_tokens=budget.available_context_tokens,
            compression_profile=profile,
        )
        if usage_entry:
            compression_usage_ledger.append(usage_entry)
        if compressed_entries:
            packed_entries = _fit_entries_to_budget(compressed_entries, budget.available_context_tokens)
            compression_mode = "compressive"

    if not packed_entries:
        packed_entries = _fit_entries_to_budget(raw_entries, budget.available_context_tokens)
        compression_mode = "raw"
        trigger_reason = None

    prompt_context = _format_context_from_entries(packed_entries)
    return PackedEvidence(
        budget=budget,
        chunk_ids=[str(entry.get("chunk_id") or "").strip() for entry in packed_entries if str(entry.get("chunk_id") or "").strip()],
        prompt_context=prompt_context,
        selected_contexts=packed_entries,
        raw_context_token_estimate=raw_context_token_estimate,
        packed_context_token_estimate=estimate_text_tokens(prompt_context),
        compression_triggered=True,
        compression_trigger_reason=trigger_reason,
        compression_mode=compression_mode,
        compression_model=compression_model if compression_mode == "compressive" else None,
        extractive_segment_count=len(extractive_entries),
        packed_evidence_count=len(packed_entries),
        compression_usage_ledger=compression_usage_ledger,
    )

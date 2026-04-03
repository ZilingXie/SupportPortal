from __future__ import annotations

from typing import Any


def _clean_excerpt(text: Any, *, max_chars: int) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return ""
    limit = max(1, int(max_chars))
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _safe_similarity(value: Any) -> float | None:
    try:
        similarity = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, similarity)), 4)


def build_rag_evidence_summary(
    *,
    quality_signals: dict[str, Any] | None,
    selected_contexts: list[dict[str, Any]] | None,
    cited_chunk_ids: set[str] | None = None,
    query_understanding: dict[str, Any] | None = None,
    max_contexts: int = 3,
    max_excerpt_chars: int = 280,
) -> dict[str, Any]:
    normalized_quality = {
        "generation_mode": None,
        "selected_doc_count": None,
        "citation_coverage_ratio": None,
        "top1_similarity_score": None,
        "avg_selected_similarity_score": None,
        "handoff_reason": None,
        "needs_human": None,
        "context_budget_enabled": None,
        "context_window": None,
        "reserved_output_tokens": None,
        "buffer_tokens": None,
        "raw_context_token_estimate": None,
        "packed_context_token_estimate": None,
        "compression_triggered": None,
        "compression_trigger_reason": None,
        "compression_mode": None,
        "compression_model": None,
        "extractive_segment_count": None,
        "packed_evidence_count": None,
    }
    if isinstance(quality_signals, dict):
        for key in normalized_quality:
            normalized_quality[key] = quality_signals.get(key)

    normalized_contexts: list[dict[str, Any]] = []
    cited_ids = {str(chunk_id).strip() for chunk_id in (cited_chunk_ids or set()) if str(chunk_id).strip()}
    for item in (selected_contexts or [])[: max(1, int(max_contexts))]:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("chunk_id") or "").strip()
        excerpt = _clean_excerpt(
            item.get("text_excerpt") if item.get("text_excerpt") is not None else item.get("text"),
            max_chars=max_excerpt_chars,
        )
        normalized_contexts.append(
            {
                "chunk_id": chunk_id,
                "heading": str(item.get("heading") or "").strip(),
                "source_path": str(item.get("source_path") or "").strip(),
                "source_url": str(item.get("source_url") or "").strip(),
                "text_excerpt": excerpt,
                "similarity": _safe_similarity(item.get("similarity")),
                "cited_in_answer": chunk_id in cited_ids,
            }
        )

    payload = {
        "quality_signals": normalized_quality,
        "selected_contexts": normalized_contexts,
    }
    if isinstance(query_understanding, dict) and query_understanding:
        payload["query_understanding"] = {
            "query_understanding_enabled": bool(query_understanding.get("query_understanding_enabled")),
            "query_understanding_version": query_understanding.get("query_understanding_version"),
            "query_profile": query_understanding.get("query_profile"),
            "glossary_version": query_understanding.get("glossary_version"),
            "self_query_version": query_understanding.get("self_query_version"),
            "fallback_mode": query_understanding.get("fallback_mode"),
            "glossary_hit_terms": list(query_understanding.get("glossary_hit_terms") or []),
            "applied_hard_filters": dict(query_understanding.get("applied_hard_filters") or {}),
            "applied_soft_signals": dict(query_understanding.get("applied_soft_signals") or {}),
            "rewritten_queries": list(query_understanding.get("rewritten_queries") or []),
            "decomposition_subqueries": list(query_understanding.get("decomposition_subqueries") or []),
        }
    return payload

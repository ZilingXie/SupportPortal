from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from backend.services.embedding_provider import (
    DEFAULT_PGVECTOR_TABLE,
    embedding_model_id,
    embedding_provider_name,
    get_embedding_provider,
)

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

SYSTEM_PROMPT = """You are a technical support documentation QA assistant.

Rules:
1) Use only the provided context chunks.
2) If evidence is insufficient, set "insufficient_evidence" to true and answer exactly:
   "{insufficient_reply}"
3) Do not fabricate APIs, versions, parameters, or steps.
4) Every factual claim must be supported by citations.
5) Output must be valid JSON only, no markdown fences.
""".format(insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY)


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
    error_flag: bool = False
    timeout_flag: bool = False
    error_type: str | None = None


@dataclass
class RagQueryResult:
    answer: RagAnswer
    trace: RagQueryTrace


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


def _get_rag_config(top_k: int | None = None) -> dict[str, Any]:
    dsn = (os.getenv("PGVECTOR_DSN") or "").strip()
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    final_top_k = max(1, int(top_k)) if top_k is not None else _safe_int_env("RAG_TOP_K", 6)
    vector_candidate_k = max(20, final_top_k * 4)
    keyword_candidate_k = max(20, final_top_k * 4)
    fusion_candidate_k = max(30, final_top_k * 5)
    schema = (os.getenv("PGVECTOR_SCHEMA") or "supportportal").strip() or "supportportal"
    raw_table = (os.getenv("PGVECTOR_TABLE") or DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE
    table_name = raw_table if "." in raw_table else f"{schema}.{raw_table}"
    return {
        "dsn": dsn,
        "api_key": api_key,
        "table": table_name,
        "top_k": final_top_k,
        "vector_candidate_k": vector_candidate_k,
        "keyword_candidate_k": keyword_candidate_k,
        "fusion_candidate_k": fusion_candidate_k,
        "chat_model": (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4.1").strip(),
        "embedding_provider": embedding_provider_name(),
        "embedding_model": embedding_model_id(),
        "request_timeout_seconds": _safe_float_env("RAG_REQUEST_TIMEOUT_SECONDS", 20.0),
        "max_retries": _safe_int_env("RAG_OPENAI_MAX_RETRIES", 1),
    }


def _table_identifier(sql: Any, raw_table: str) -> Any:
    schema, table_name = _split_table_name(raw_table)
    return sql.Identifier(schema, table_name)


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
                source_type=(str(row[8]).strip() or None) if row[8] is not None else None,
                chunk_strategy=(str(row[9]).strip() or None) if row[9] is not None else None,
                similarity=float(row[10]) if row[10] is not None else 0.0,
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
        rank = float(row[10]) if row[10] is not None else 0.0
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
                source_type=(str(row[8]).strip() or None) if row[8] is not None else None,
                chunk_strategy=(str(row[9]).strip() or None) if row[9] is not None else None,
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
            source_type=(str(row[8]).strip() or None) if row[8] is not None else None,
            chunk_strategy=(str(row[9]).strip() or None) if row[9] is not None else None,
            similarity=0.0,
        )
        hits = _keyword_hit_count(_chunk_search_text(chunk), terms)
        if hits <= 0:
            continue
        chunk.similarity = min(1.0, hits / max(1, len(terms)))
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
    keyword_chunks: list[RetrievedChunk],
    *,
    limit: int,
) -> list[RetrievedChunk]:
    rrf_k = 60.0
    merged_scores: dict[str, float] = {}
    merged_chunks: dict[str, RetrievedChunk] = {}

    for ranked_chunks in [vector_chunks, keyword_chunks]:
        for index, chunk in enumerate(ranked_chunks, start=1):
            dedupe_key = chunk.chunk_id or f"{chunk.source_path}:{chunk.text[:120]}"
            merged_scores[dedupe_key] = merged_scores.get(dedupe_key, 0.0) + (1.0 / (rrf_k + index))
            existing = merged_chunks.get(dedupe_key)
            if existing is None or chunk.similarity > existing.similarity:
                merged_chunks[dedupe_key] = chunk

    ordered = sorted(
        merged_scores.items(),
        key=lambda item: (item[1], merged_chunks[item[0]].similarity),
        reverse=True,
    )
    results: list[RetrievedChunk] = []
    for dedupe_key, _score in ordered[: max(1, int(limit))]:
        results.append(merged_chunks[dedupe_key])
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
    return f"""Question:
{question}

Context Chunks:
{context_block}

Return JSON with this exact schema:
{{
  "answer": "string",
  "key_steps": ["string"],
  "citations": ["chunk_id"],
  "insufficient_evidence": false
}}

Requirements:
- "citations" must contain chunk_id values that exist in Context Chunks.
- If insufficient evidence, return:
  {{
    "answer": "{INSUFFICIENT_EVIDENCE_REPLY}",
    "key_steps": [],
    "citations": [],
    "insufficient_evidence": true
  }}
"""


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
        "I found related knowledge base content, but I could not produce a fully grounded structured response.",
        "",
        "Key Steps:",
    ]
    for index, chunk in enumerate(chunks[:3], start=1):
        snippet = " ".join(chunk.text.split())
        lines.append(f"{index}. {snippet[:220]}")
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
    sources: list[str] = [f"rag:{chunk.chunk_id}" for chunk in chunks[:3] if chunk.chunk_id]
    if not sources:
        sources = [f"rag:{chunk.source_path}" for chunk in chunks[:3] if chunk.source_path]
    citations = _citation_records_from_chunks(chunks, limit=3)
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
    prompt = _build_answer_prompt(message, context_block)
    if strict_retry:
        prompt += (
            "\n\nRetry requirement:\n"
            "- Return JSON only.\n"
            "- Every citation must be one of the provided chunk ids.\n"
            f'- If unsure, use this exact insufficient answer: "{INSUFFICIENT_EVIDENCE_REPLY}".\n'
        )

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
    prompt = _build_answer_prompt(message, context_block)
    if strict_retry:
        prompt += (
            "\n\nRetry requirement:\n"
            "- Return JSON only.\n"
            "- Every citation must be one of the provided chunk ids.\n"
            f'- If unsure, use this exact insufficient answer: "{INSUFFICIENT_EVIDENCE_REPLY}".\n'
        )

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
    if not config["dsn"] or not config["api_key"]:
        return None

    provider = get_embedding_provider()
    vector_chunks: list[RetrievedChunk] = []
    keyword_chunks: list[RetrievedChunk] = []
    chunks: list[RetrievedChunk] = []
    embedding_request_meta: list[dict[str, Any]] = []
    embedding_dimensions = getattr(provider, "vector_dim", None)
    query_type = _infer_query_type(message)
    total_started_at = time.perf_counter()
    vector_latency_ms = 0.0
    bm25_latency_ms = 0.0
    generation_latency_ms = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    model_name: str | None = None

    try:
        vector_started_at = time.perf_counter()
        vector_chunks = _retrieve_chunks(
            message,
            config,
            limit=int(config["vector_candidate_k"]),
        )
        vector_latency_ms = round((time.perf_counter() - vector_started_at) * 1000, 2)
    except Exception as exc:
        logger.warning("RAG retrieval failed: %s", exc)
    finally:
        embedding_request_meta.extend(_drain_embedding_request_meta(provider))

    try:
        bm25_started_at = time.perf_counter()
        keyword_chunks = _retrieve_fts_chunks(
            message,
            config,
            limit=int(config["keyword_candidate_k"]),
        )
        bm25_latency_ms = round((time.perf_counter() - bm25_started_at) * 1000, 2)
    except Exception as exc:
        logger.warning("RAG FTS retrieval failed: %s", exc)
        try:
            bm25_started_at = time.perf_counter()
            keyword_chunks = _retrieve_keyword_chunks(
                message,
                config,
                limit=int(config["keyword_candidate_k"]),
            )
            bm25_latency_ms = round((time.perf_counter() - bm25_started_at) * 1000, 2)
        except Exception as keyword_exc:
            logger.warning("RAG keyword retrieval failed: %s", keyword_exc)
            keyword_chunks = []

    if not vector_chunks and not keyword_chunks:
        try:
            bm25_started_at = time.perf_counter()
            keyword_chunks = _retrieve_keyword_chunks(
                message,
                config,
                limit=int(config["keyword_candidate_k"]),
            )
            bm25_latency_ms = round((time.perf_counter() - bm25_started_at) * 1000, 2)
        except Exception as exc:
            logger.warning("RAG keyword retrieval failed: %s", exc)
            keyword_chunks = []

    if vector_chunks or keyword_chunks:
        chunks = _rrf_merge(
            vector_chunks,
            keyword_chunks,
            limit=int(config["fusion_candidate_k"]),
        )
        if not chunks:
            chunks = _merge_chunks(
                vector_chunks,
                keyword_chunks,
                limit=int(config["fusion_candidate_k"]),
            )

    if not chunks:
        try:
            bm25_started_at = time.perf_counter()
            chunks = _retrieve_keyword_chunks(
                message,
                config,
                limit=int(config["top_k"]),
            )
            bm25_latency_ms = round((time.perf_counter() - bm25_started_at) * 1000, 2)
        except Exception as exc:
            logger.warning("RAG keyword retrieval failed: %s", exc)
            chunks = []
        if not chunks:
            answer = RagAnswer(
                answer=INSUFFICIENT_EVIDENCE_REPLY,
                confidence=0.55,
                sources=[],
                citations=[],
            )
            trace = RagQueryTrace(
                query_type=query_type,
                retrieval_strategy="bm25_only" if keyword_chunks else "vector_only",
                vector_candidates_count=len(vector_chunks),
                bm25_candidates_count=len(keyword_chunks),
                reranked_candidates_count=0,
                retrieved_chunk_ids=[],
                selected_chunk_ids=[],
                vector_retrieval_latency_ms=vector_latency_ms,
                bm25_retrieval_latency_ms=bm25_latency_ms,
                retrieval_latency_ms=round(vector_latency_ms + bm25_latency_ms, 2),
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
            )
            return RagQueryResult(answer=answer, trace=trace)

    final_chunks = chunks[: int(config["top_k"])] or chunks
    allowed_chunk_ids = {chunk.chunk_id for chunk in final_chunks}
    payload: dict[str, Any] | None = None
    try:
        generation_started_at = time.perf_counter()
        payload, prompt_tokens, completion_tokens, model_name = _invoke_llm_payload_with_trace(
            message,
            final_chunks,
            config,
            strict_retry=False,
        )
        if payload is None or not _is_valid_response(payload, allowed_chunk_ids):
            payload, prompt_tokens, completion_tokens, model_name = _invoke_llm_payload_with_trace(
                message,
                final_chunks,
                config,
                strict_retry=True,
            )
        generation_latency_ms = round((time.perf_counter() - generation_started_at) * 1000, 2)
    except Exception as exc:
        logger.warning("RAG answer generation failed: %s", exc)

    def _trace_for(answer: RagAnswer, *, needs_human: bool, handoff_reason: str | None) -> RagQueryTrace:
        cited_chunk_ids = [str(item.get("chunk_id")) for item in answer.citations if isinstance(item, dict) and item.get("chunk_id")]
        return RagQueryTrace(
            query_type=query_type,
            retrieval_strategy="hybrid_rrf" if vector_chunks and keyword_chunks else ("vector_only" if vector_chunks else "bm25_only"),
            vector_candidates_count=len(vector_chunks),
            bm25_candidates_count=len(keyword_chunks),
            reranked_candidates_count=0,
            retrieved_chunk_ids=[chunk.chunk_id for chunk in chunks if chunk.chunk_id],
            selected_chunk_ids=[chunk.chunk_id for chunk in final_chunks if chunk.chunk_id],
            vector_retrieval_latency_ms=vector_latency_ms,
            bm25_retrieval_latency_ms=bm25_latency_ms,
            retrieval_latency_ms=round(vector_latency_ms + bm25_latency_ms, 2),
            rerank_latency_ms=0.0,
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
        )

    if payload is not None and _is_valid_response(payload, allowed_chunk_ids):
        if payload["insufficient_evidence"] is True:
            if _has_grounded_keyword_overlap(message, final_chunks):
                logger.info(
                    "RAG insufficient evidence but keyword overlap was found. "
                    "Using extractive fallback."
                )
                answer = _build_extractive_rag_answer(final_chunks)
                return RagQueryResult(answer=answer, trace=_trace_for(answer, needs_human=False, handoff_reason=None))
            answer = RagAnswer(
                answer=INSUFFICIENT_EVIDENCE_REPLY,
                confidence=0.55,
                sources=[],
                citations=[],
            )
            return RagQueryResult(
                answer=answer,
                trace=_trace_for(answer, needs_human=True, handoff_reason="insufficient_evidence"),
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
        return RagQueryResult(answer=answer, trace=_trace_for(answer, needs_human=False, handoff_reason=None))

    logger.warning("RAG structured answer invalid, using extractive fallback.")
    answer = _build_extractive_rag_answer(final_chunks)
    return RagQueryResult(answer=answer, trace=_trace_for(answer, needs_human=False, handoff_reason=None))


def answer_with_rag(message: str, top_k: int | None = None) -> RagAnswer | None:
    """
    Attempt to answer with PostgreSQL pgvector retrieval + LangChain answer generation.
    Returns None when RAG is not configured or retrieval fails, so caller can fallback.
    """
    result = run_rag_query(message, top_k=top_k)
    return result.answer if result is not None else None

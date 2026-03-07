from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)
_UNAVAILABLE_MODELS: set[str] = set()

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
    h1: str | None = None
    h2: str | None = None
    h3: str | None = None
    source_url: str | None = None


@dataclass
class RagAnswer:
    answer: str
    confidence: float
    sources: list[str]
    citations: list[dict[str, str]]


def _import_langchain() -> tuple[Any, Any]:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    return ChatOpenAI, OpenAIEmbeddings


def _import_psycopg() -> Any:
    import psycopg

    return psycopg


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.10f}" for v in values) + "]"


def _safe_int_env(key: str, default_value: int) -> int:
    raw = (os.getenv(key, "") or "").strip()
    if not raw:
        return default_value
    try:
        parsed = int(raw)
    except ValueError:
        return default_value
    return parsed if parsed > 0 else default_value


def _build_heading(chunk: RetrievedChunk) -> str:
    heading_items = [item for item in [chunk.h1, chunk.h2, chunk.h3] if item]
    return " > ".join(heading_items) if heading_items else "Unknown heading"


def _get_rag_config() -> dict[str, Any]:
    dsn = (os.getenv("PGVECTOR_DSN") or os.getenv("DATABASE_URL") or "").strip()
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    return {
        "dsn": dsn,
        "api_key": api_key,
        "table": (os.getenv("PGVECTOR_TABLE") or "docagent_chunks").strip(),
        "top_k": _safe_int_env("RAG_TOP_K", 5),
        "chat_model": (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4.1").strip(),
        "embedding_model": (
            os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-large"
        ).strip(),
    }


def _retrieve_chunks(message: str, config: dict[str, Any]) -> list[RetrievedChunk]:
    _, OpenAIEmbeddings = _import_langchain()
    psycopg = _import_psycopg()
    sql = psycopg.sql

    embeddings = OpenAIEmbeddings(
        model=config["embedding_model"],
        api_key=config["api_key"],
    )
    query_embedding = embeddings.embed_query(message)
    vector_param = _vector_literal(query_embedding)

    query = sql.SQL(
        """
        SELECT
            id,
            content,
            source_path,
            h1,
            h2,
            h3,
            source_url,
            1 - (embedding <=> %s::vector) AS similarity
        FROM {}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """
    ).format(sql.Identifier(config["table"]))

    with psycopg.connect(config["dsn"]) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (vector_param, vector_param, int(config["top_k"])))
            rows = cur.fetchall()

    chunks: list[RetrievedChunk] = []
    for row in rows:
        chunks.append(
            RetrievedChunk(
                chunk_id=str(row[0]),
                text=str(row[1]),
                source_path=str(row[2]),
                h1=(str(row[3]).strip() or None) if row[3] is not None else None,
                h2=(str(row[4]).strip() or None) if row[4] is not None else None,
                h3=(str(row[5]).strip() or None) if row[5] is not None else None,
                source_url=(str(row[6]).strip() or None) if row[6] is not None else None,
                similarity=float(row[7]) if row[7] is not None else 0.0,
            )
        )
    return chunks


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


def _invoke_llm_payload(
    message: str,
    chunks: list[RetrievedChunk],
    config: dict[str, Any],
    strict_retry: bool = False,
) -> dict[str, Any] | None:
    ChatOpenAI, _ = _import_langchain()
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


def _confidence_from_chunks(chunks: list[RetrievedChunk]) -> float:
    if not chunks:
        return 0.0
    best_similarity = max(0.0, min(1.0, chunks[0].similarity))
    confidence = 0.72 + (0.2 * best_similarity) + (0.02 * min(len(chunks), 5))
    return round(min(0.95, confidence), 2)


def answer_with_rag(message: str) -> RagAnswer | None:
    """
    Attempt to answer with PostgreSQL pgvector retrieval + LangChain answer generation.
    Returns None when RAG is not configured or retrieval fails, so caller can fallback.
    """
    config = _get_rag_config()
    if not config["dsn"] or not config["api_key"]:
        return None

    try:
        chunks = _retrieve_chunks(message, config)
    except Exception as exc:
        logger.warning("RAG retrieval failed: %s", exc)
        return None

    if not chunks:
        return RagAnswer(
            answer=INSUFFICIENT_EVIDENCE_REPLY,
            confidence=0.55,
            sources=[],
            citations=[],
        )

    allowed_chunk_ids = {chunk.chunk_id for chunk in chunks}
    payload: dict[str, Any] | None = None
    try:
        payload = _invoke_llm_payload(message, chunks, config, strict_retry=False)
        if payload is None or not _is_valid_response(payload, allowed_chunk_ids):
            payload = _invoke_llm_payload(message, chunks, config, strict_retry=True)
    except Exception as exc:
        logger.warning("RAG answer generation failed: %s", exc)

    if payload is not None and _is_valid_response(payload, allowed_chunk_ids):
        if payload["insufficient_evidence"] is True:
            return RagAnswer(
                answer=INSUFFICIENT_EVIDENCE_REPLY,
                confidence=0.55,
                sources=[],
                citations=[],
            )
        citations = [str(chunk_id) for chunk_id in payload["citations"]]
        citation_records = _citation_records_from_ids(citations, chunks)
        sources = [
            record.get("source_url") or f"rag:{record['chunk_id']}"
            for record in citation_records
        ]
        return RagAnswer(
            answer=_build_answer_text(str(payload["answer"]), payload.get("key_steps", [])),
            confidence=_confidence_from_chunks(chunks),
            sources=sources,
            citations=citation_records,
        )

    logger.warning("RAG structured answer invalid, using extractive fallback.")
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

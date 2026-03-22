from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from backend.services.rag_tokenizer import build_bm25_document_text, tokenize_bm25_text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_bm25_index_payload(
    *,
    rows: list[dict[str, Any]],
    index_role: str,
) -> dict[str, Any]:
    normalized_role = str(index_role or "").strip().lower() or "primary"
    if normalized_role != "primary":
        return {
            "docs": [],
            "postings": [],
            "terms": [],
            "stats": {
                "index_role": normalized_role,
                "doc_count": 0,
                "avg_doc_length": 0.0,
            },
        }

    docs: list[dict[str, Any]] = []
    postings: list[dict[str, Any]] = []
    term_doc_freq: Counter[str] = Counter()
    doc_lengths: list[int] = []

    for row in rows:
        chunk_id = str(row.get("id") or "").strip()
        doc_id = str(row.get("doc_id") or "").strip()
        if not chunk_id or not doc_id:
            continue
        bm25_text = build_bm25_document_text(
            h1=row.get("h1"),
            h2=row.get("h2"),
            h3=row.get("h3"),
            content=row.get("content"),
        )
        tokens = tokenize_bm25_text(bm25_text)
        term_counts = Counter(tokens)
        doc_length = sum(term_counts.values())
        doc_lengths.append(doc_length)
        docs.append(
            {
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "index_role": normalized_role,
                "doc_length": doc_length,
                "updated_at": str(row.get("updated_at") or row.get("vector_indexed_at") or _utc_now()),
            }
        )
        for term, tf in term_counts.items():
            postings.append(
                {
                    "chunk_id": chunk_id,
                    "term": term,
                    "tf": int(tf),
                    "index_role": normalized_role,
                }
            )
            term_doc_freq[term] += 1

    terms = [
        {
            "term": term,
            "index_role": normalized_role,
            "doc_freq": int(doc_freq),
        }
        for term, doc_freq in sorted(term_doc_freq.items())
    ]
    avg_doc_length = (sum(doc_lengths) / len(doc_lengths)) if doc_lengths else 0.0
    return {
        "docs": docs,
        "postings": postings,
        "terms": terms,
        "stats": {
            "index_role": normalized_role,
            "doc_count": len(docs),
            "avg_doc_length": float(avg_doc_length),
        },
    }

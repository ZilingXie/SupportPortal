"""Unified knowledge retrieval for the investigation Agent (bridge plan r1).

WeKnora is the PRIMARY retrieval source: only objects the fork has published,
parsed and enabled (``parse_status=completed`` + ``enable_status=enabled``)
may enter Agent context — draft/unindexed/version-unknown objects are skipped
(C3/C8), and a readback missing lifecycle fields is treated as NOT ready
because the fork always serializes them (C10 fail-closed).

AgentMemory is the MIGRATION-PERIOD FALLBACK only: it runs when WeKnora is
unavailable through transport/5xx/malformed-response/auth failures (C5). A
VALID empty WeKnora result means "no hits" and never triggers the fallback
(C6). When both sides are unavailable the caller receives an explicit
escalate-human verdict instead of an evidence-free answer (C7).
"""

from __future__ import annotations

from typing import Any

from backend.services.weknora_client import (
    WeKnoraClient,
    WeKnoraError,
    weknora_readback_is_ready,
)

RETRIEVAL_SOURCE_WEKNORA = "weknora"
RETRIEVAL_SOURCE_FALLBACK = "agent_memory_fallback"
RETRIEVAL_SOURCE_ESCALATE = "escalate_human"

# Only these WeKnora failure kinds count as "WeKnora unavailable" (C5); the
# client maps HTTP 5xx to failure_kind="http" with status_code >= 500.
_WEKNORA_UNAVAILABLE_KINDS = frozenset({"timeout", "transport", "auth", "http"})


def _weknora_unavailable(exc: WeKnoraError) -> bool:
    if exc.failure_kind == "http":
        return exc.status_code is not None and exc.status_code >= 500
    return exc.failure_kind in _WEKNORA_UNAVAILABLE_KINDS


def _lineage_from_read(read: dict[str, Any]) -> dict[str, str]:
    payload = read.get("payload") if isinstance(read.get("payload"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    lineage = metadata.get("lineage") if isinstance(metadata.get("lineage"), dict) else {}
    return {str(k): str(v) for k, v in lineage.items() if str(v or "").strip()}


def _weknora_evidence_entry(read: dict[str, Any]) -> dict[str, Any]:
    lineage = _lineage_from_read(read)
    content = str(read.get("content") or "").strip()
    return {
        "retrieval_source": RETRIEVAL_SOURCE_WEKNORA,
        "weknora_object_id": str(read.get("object_id") or ""),
        "content_version": str(read.get("version") or ""),
        "content_hash": lineage.get("content_hash", ""),
        "source_type": lineage.get("source_type", ""),
        "source_id": lineage.get("source_id", ""),
        "title": str(read.get("title") or ""),
        "evidence": content[:2000],
    }


def retrieve_knowledge_evidence(
    query: str,
    *,
    weknora_client: WeKnoraClient,
    agent_memory_client: Any = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """Return ``{retrieval_source, results, fallback_reason?}`` per C4-C7.

    WeKnora hits are individually read back by object id and only READY
    objects survive (C3/C8/C10). Non-ready hits are skipped, never errored:
    the fork indexes asynchronously and a still-parsing object simply is not
    consumable knowledge yet."""
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return {
            "retrieval_source": RETRIEVAL_SOURCE_ESCALATE,
            "results": [],
            "fallback_reason": "empty query",
        }

    results: list[dict[str, Any]] = []
    weknora_error: WeKnoraError | None = None
    try:
        hits = weknora_client.knowledge_search(query=normalized_query, top_k=top_k)
        for hit in hits[:top_k]:
            object_id = str(hit.get("object_id") or "").strip()
            if not object_id:
                continue
            try:
                read = weknora_client.knowledge_read(object_id=object_id)
            except WeKnoraError as exc:
                if _weknora_unavailable(exc) or exc.failure_kind == "invalid_response":
                    # A malformed per-hit readback makes the WeKnora surface
                    # unverifiable.  It is not evidence of a valid empty
                    # result, so route the whole query through the migration
                    # fallback (or human escalation).
                    raise
                continue  # an absent/non-ready hit can be skipped safely
            if not weknora_readback_is_ready(read):
                continue
            if not str(read.get("content") or "").strip():
                continue
            results.append(_weknora_evidence_entry(read))
    except WeKnoraError as exc:
        if not _weknora_unavailable(exc):
            # Malformed responses are a WeKnora unavailability too (C5):
            # guessing at a half-parsed body would fabricate evidence.
            if exc.failure_kind != "invalid_response":
                raise
        weknora_error = exc

    if weknora_error is None:
        return {"retrieval_source": RETRIEVAL_SOURCE_WEKNORA, "results": results}

    if agent_memory_client is None:
        return {
            "retrieval_source": RETRIEVAL_SOURCE_ESCALATE,
            "results": [],
            "fallback_reason": f"weknora_unavailable: {weknora_error.failure_kind}; no fallback configured",
        }
    try:
        am = agent_memory_client.search_knowledge(normalized_query, top_k=top_k)
    except Exception:  # noqa: BLE001 - both sides down is an explicit verdict (C7)
        return {
            "retrieval_source": RETRIEVAL_SOURCE_ESCALATE,
            "results": [],
            "fallback_reason": (
                f"weknora_unavailable: {weknora_error.failure_kind}; "
                "agent_memory_unavailable"
            ),
        }
    hits = am.get("hits") if isinstance(am, dict) else None
    if not isinstance(hits, list):
        return {
            "retrieval_source": RETRIEVAL_SOURCE_ESCALATE,
            "results": [],
            "fallback_reason": "agent_memory malformed response",
        }
    fallback_results: list[dict[str, Any]] = []
    for hit in hits[:top_k]:
        if not isinstance(hit, dict):
            continue
        fallback_results.append({
            "retrieval_source": RETRIEVAL_SOURCE_FALLBACK,
            "title": str(hit.get("title") or ""),
            "evidence": str(hit.get("excerpt") or hit.get("content") or "")[:2000],
            "source_type": "agent_memory_wiki",
            "source_id": str(hit.get("wiki_id") or hit.get("page_id") or ""),
        })
    return {
        "retrieval_source": RETRIEVAL_SOURCE_FALLBACK,
        "results": fallback_results,
        "fallback_reason": f"weknora_unavailable: {weknora_error.failure_kind}",
    }

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OFFICIAL_KNOWLEDGE_TYPE = "official"
OFFICIAL_SOURCE_TYPE = "official_markdown_upload"

RAG_ACCESS_OFFICIAL_ONLY = "official_only"
RAG_ACCESS_NON_OFFICIAL_ONLY = "non_official_only"


@dataclass(frozen=True)
class RagMetadataAccessFilter:
    include: dict[str, str]
    exclude: dict[str, str]


def _normalized_token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def normalize_rag_access_mode(value: Any) -> str | None:
    normalized = _normalized_token(value)
    if normalized == RAG_ACCESS_OFFICIAL_ONLY:
        return RAG_ACCESS_OFFICIAL_ONLY
    if normalized == RAG_ACCESS_NON_OFFICIAL_ONLY:
        return RAG_ACCESS_NON_OFFICIAL_ONLY
    return None


def metadata_filter_for_access_mode(value: Any) -> RagMetadataAccessFilter:
    mode = normalize_rag_access_mode(value)
    if mode == RAG_ACCESS_OFFICIAL_ONLY:
        return RagMetadataAccessFilter(
            include={
                "knowledge_type": OFFICIAL_KNOWLEDGE_TYPE,
                "source_type": OFFICIAL_SOURCE_TYPE,
            },
            exclude={},
        )
    if mode == RAG_ACCESS_NON_OFFICIAL_ONLY:
        return RagMetadataAccessFilter(
            include={},
            exclude={
                "knowledge_type": OFFICIAL_KNOWLEDGE_TYPE,
                "source_type": OFFICIAL_SOURCE_TYPE,
            },
        )
    return RagMetadataAccessFilter(include={}, exclude={})


def is_official_metadata(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    knowledge_type = str(metadata.get("knowledge_type") or "").strip().lower()
    source_type = str(metadata.get("source_type") or "").strip().lower()
    return knowledge_type == OFFICIAL_KNOWLEDGE_TYPE and source_type == OFFICIAL_SOURCE_TYPE

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KG_AUXILIARY_MODE = "client_rag_auxiliary"
KG_OFFICIAL_DOC_KNOWLEDGE_TYPE = "official"
KG_OFFICIAL_DOC_SOURCE_TYPE = "official_markdown_upload"

_CASE_MEMORY_KEYS = {
    "case_memory_ledger_id",
    "case_memory_id",
    "engineer_case_id",
    "client_ticket_id",
}
_CASE_MEMORY_VALUE_MARKERS = {
    "case_memory",
    "confirmed_case",
    "active_memory",
    "ledger_candidate",
}


@dataclass(frozen=True)
class OfficialDocKgIngestPlan:
    """First-phase KG scope: official docs only, used as Client AI RAG assistance."""

    mode: str
    knowledge_type: str
    source_type: str
    ingestion_id: str | None
    document_id: str | None
    title: str | None
    source_url: str | None
    provenance_required: bool = True
    allow_answer_without_rag_citation: bool = False
    includes_confirmed_case_memory: bool = False


def build_official_doc_kg_ingest_plan(record: dict[str, Any]) -> OfficialDocKgIngestPlan | None:
    """Return a KG ingest plan only for official docs eligible for first-phase KG.

    This function intentionally does not normalize missing or unknown source types into
    official docs. The KG first phase must not accidentally ingest confirmed cases,
    technical articles, or benchmark data through repository defaults.
    """

    if not isinstance(record, dict):
        return None
    if _clean_text(record.get("knowledge_type")).lower() != KG_OFFICIAL_DOC_KNOWLEDGE_TYPE:
        return None
    if _clean_text(record.get("source_type")).lower() != KG_OFFICIAL_DOC_SOURCE_TYPE:
        return None
    if _has_case_memory_marker(record):
        return None

    return OfficialDocKgIngestPlan(
        mode=KG_AUXILIARY_MODE,
        knowledge_type=KG_OFFICIAL_DOC_KNOWLEDGE_TYPE,
        source_type=KG_OFFICIAL_DOC_SOURCE_TYPE,
        ingestion_id=_optional_text(record.get("ingestion_id")),
        document_id=_optional_text(record.get("document_id")),
        title=_optional_text(record.get("title")),
        source_url=_optional_text(record.get("source_url")),
    )


def _has_case_memory_marker(record: dict[str, Any]) -> bool:
    if any(_optional_text(record.get(key)) for key in _CASE_MEMORY_KEYS):
        return True

    metadata = record.get("request_metadata")
    values: list[Any] = [
        record.get("source_type"),
        record.get("entry_type"),
        record.get("memory_type"),
        record.get("memory_status"),
        record.get("active_memory_status"),
    ]
    if isinstance(metadata, dict):
        values.extend(metadata.values())

    return any(_contains_case_memory_marker(value) for value in values)


def _contains_case_memory_marker(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_case_memory_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_case_memory_marker(item) for item in value)
    normalized = _clean_text(value).lower()
    return any(marker in normalized for marker in _CASE_MEMORY_VALUE_MARKERS)


def _optional_text(value: Any) -> str | None:
    normalized = _clean_text(value)
    return normalized or None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()

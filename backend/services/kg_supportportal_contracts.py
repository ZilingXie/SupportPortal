"""SupportPortal KG contracts — focused data models for the KG auxiliary layer.

All KG outputs MUST carry provenance (chunk_id, source_url, document_id,
schema_version). Without provenance an output must not enter runtime context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Envelope / input types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OfficialDocKgChunkInput:
    """A single official-doc chunk ready for KG ingestion.

    Every field except `text` and `metadata` is provenance-critical.
    """

    chunk_id: str
    document_id: str
    source_url: str
    schema_version: str
    text: str
    content_hash: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provenance (must survive round-trip through KG)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KgProvenance:
    chunk_id: str
    source_url: str
    document_id: str
    schema_version: str


# ---------------------------------------------------------------------------
# Ingestion result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KgIngestResult:
    chunk_id: str
    ok: bool
    error: str | None = None
    provenance: KgProvenance | None = None


# ---------------------------------------------------------------------------
# Runtime auxiliary outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KgExpansion:
    """A single query-expansion term sourced from KG."""

    term: str
    provenance: KgProvenance
    entity_type: str | None = None
    relation: str | None = None


@dataclass(frozen=True)
class KgRerankSignal:
    """A small boost signal for a specific RAG chunk."""

    chunk_id: str
    boost: float  # additive, in [0, KG_RERANK_BOOST_MAX]
    provenance: KgProvenance
    reason: str | None = None


@dataclass(frozen=True)
class KgStructuredFact:
    """A structured fact derived from KG that supplements RAG context."""

    text: str
    provenance: KgProvenance
    entity_type: str | None = None
    relation: str | None = None
    confidence: float | None = None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KgValidationError:
    field: str
    message: str
    value: Any = None


_REQUIRED_PROVENANCE_FIELDS = ("source_url", "document_id", "chunk_id", "schema_version")


def validate_provenance(obj: object, *, label: str = "kg_output") -> list[KgValidationError]:
    """Check that every required provenance field is present and non-empty.

    KG runtime outputs carry a nested `provenance` envelope, while the
    envelope itself stores the fields directly. Accept both shapes so callers
    can validate either a `KgProvenance` or a KG output object.
    """

    errors: list[KgValidationError] = []
    target = getattr(obj, "provenance", None)
    if target is None:
        if all(hasattr(obj, field_name) for field_name in _REQUIRED_PROVENANCE_FIELDS):
            target = obj
        else:
            return [
                KgValidationError(
                    field=f"{label}.provenance",
                    message="Missing required provenance envelope",
                    value=None,
                )
            ]
    for field_name in _REQUIRED_PROVENANCE_FIELDS:
        value = getattr(target, field_name, None)
        if not value or not isinstance(value, str) or not value.strip():
            errors.append(
                KgValidationError(
                    field=f"{label}.provenance.{field_name}",
                    message=f"Missing or empty required provenance field '{field_name}'",
                    value=value,
                )
            )
    return errors


def has_valid_provenance(obj: object) -> bool:
    return len(validate_provenance(obj)) == 0

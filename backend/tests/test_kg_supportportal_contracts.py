"""Tests for SupportPortal KG contracts — provenance validation and chunk input
construction.
"""

from __future__ import annotations

from backend.services.kg_official_docs_scope import (
    build_official_doc_kg_chunk_input,
)
from backend.services.kg_supportportal_contracts import (
    KgExpansion,
    KgProvenance,
    KgRerankSignal,
    KgStructuredFact,
    has_valid_provenance,
    validate_provenance,
)


# ---------------------------------------------------------------------------
# OfficialDocKgChunkInput construction
# ---------------------------------------------------------------------------


def _official_record(**overrides: object) -> dict:
    base: dict = {
        "ingestion_id": "ing-1",
        "knowledge_type": "official",
        "source_type": "official_markdown_upload",
        "document_id": "doc-1",
        "title": "Token Auth",
        "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


def _chunk(**overrides: object) -> dict:
    base: dict = {
        "chunk_id": "doc-1-chunk-0",
        "text": "Use a token for authentication.",
        "chunk_index": 0,
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


def test_official_chunk_normal_construction() -> None:
    inp = build_official_doc_kg_chunk_input(_official_record(), _chunk())
    assert inp is not None
    assert inp.chunk_id == "doc-1-chunk-0"
    assert inp.document_id == "doc-1"
    assert inp.source_url == "https://docs.agora.io/en/video-calling/token-authentication"
    assert inp.schema_version == "supportportal_official_docs_v1"
    assert "token" in inp.text.lower()


def test_official_chunk_preserves_structured_text() -> None:
    text = "Use token auth.\n\n```java\njoinChannel(token);\n```"
    inp = build_official_doc_kg_chunk_input(_official_record(), _chunk(text=text))
    assert inp is not None
    assert inp.text == text


def test_missing_source_url_rejected() -> None:
    inp = build_official_doc_kg_chunk_input(
        _official_record(source_url=""), _chunk()
    )
    assert inp is None


def test_missing_chunk_id_rejected() -> None:
    inp = build_official_doc_kg_chunk_input(
        _official_record(), _chunk(chunk_id="")
    )
    assert inp is None


def test_missing_document_id_rejected() -> None:
    inp = build_official_doc_kg_chunk_input(
        _official_record(document_id=""), _chunk()
    )
    assert inp is None


def test_technical_article_rejected() -> None:
    inp = build_official_doc_kg_chunk_input(
        _official_record(knowledge_type="technical", source_type="technical_article_api"),
        _chunk(),
    )
    assert inp is None


def test_confirmed_case_memory_rejected() -> None:
    inp = build_official_doc_kg_chunk_input(
        _official_record(case_memory_ledger_id="ledger-1", memory_type="confirmed_case"),
        _chunk(),
    )
    assert inp is None


def test_schema_version_affects_construction() -> None:
    inp_v1 = build_official_doc_kg_chunk_input(_official_record(), _chunk())
    assert inp_v1 is not None
    assert inp_v1.schema_version == "supportportal_official_docs_v1"

    inp_v2 = build_official_doc_kg_chunk_input(
        _official_record(), _chunk(), schema_version="supportportal_official_docs_v2"
    )
    assert inp_v2 is not None
    assert inp_v2.schema_version == "supportportal_official_docs_v2"
    assert inp_v1.schema_version != inp_v2.schema_version


def test_chunk_without_dict_is_rejected() -> None:
    inp = build_official_doc_kg_chunk_input(_official_record(), None)  # type: ignore[arg-type]
    assert inp is None


def test_record_without_dict_is_rejected() -> None:
    inp = build_official_doc_kg_chunk_input(None, _chunk())  # type: ignore[arg-type]
    assert inp is None


# ---------------------------------------------------------------------------
# Provenance validation
# ---------------------------------------------------------------------------


def _provenance(**overrides: object) -> KgProvenance:
    base = {
        "chunk_id": "c1",
        "source_url": "https://example.com/doc",
        "document_id": "d1",
        "schema_version": "v1",
    }
    base.update(overrides)  # type: ignore[arg-type]
    return KgProvenance(**base)  # type: ignore[arg-type]


def test_valid_provenance_passes() -> None:
    p = _provenance()
    assert has_valid_provenance(p)
    assert len(validate_provenance(p)) == 0


def test_missing_source_url_fails_validation() -> None:
    p = _provenance(source_url="")
    assert not has_valid_provenance(p)
    errors = validate_provenance(p)
    assert any("source_url" in e.field for e in errors)


def test_missing_chunk_id_fails_validation() -> None:
    p = _provenance(chunk_id="")
    assert not has_valid_provenance(p)


def test_missing_document_id_fails_validation() -> None:
    p = _provenance(document_id="")
    assert not has_valid_provenance(p)


def test_missing_schema_version_fails_validation() -> None:
    p = _provenance(schema_version="")
    assert not has_valid_provenance(p)


# ---------------------------------------------------------------------------
# KgExpansion provenance
# ---------------------------------------------------------------------------


def test_kg_expansion_must_have_valid_provenance() -> None:
    good = KgExpansion(term="token auth", provenance=_provenance())
    assert has_valid_provenance(good)

    bad = KgExpansion(term="orphan", provenance=_provenance(chunk_id=""))
    assert not has_valid_provenance(bad)


# ---------------------------------------------------------------------------
# KgRerankSignal provenance
# ---------------------------------------------------------------------------


def test_kg_rerank_signal_must_have_valid_provenance() -> None:
    good = KgRerankSignal(chunk_id="c1", boost=0.05, provenance=_provenance())
    assert has_valid_provenance(good)

    bad = KgRerankSignal(chunk_id="c1", boost=0.05, provenance=_provenance(source_url=""))
    assert not has_valid_provenance(bad)


# ---------------------------------------------------------------------------
# KgStructuredFact provenance
# ---------------------------------------------------------------------------


def test_kg_structured_fact_must_have_valid_provenance() -> None:
    good = KgStructuredFact(text="fact", provenance=_provenance())
    assert has_valid_provenance(good)

    bad = KgStructuredFact(text="orphan fact", provenance=_provenance(document_id=""))
    assert not has_valid_provenance(bad)


def test_output_without_provenance_envelope_fails_validation() -> None:
    errors = validate_provenance(object())
    assert errors
    assert errors[0].field == "kg_output.provenance"

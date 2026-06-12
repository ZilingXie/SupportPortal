from __future__ import annotations

from backend.services.kg_official_docs_scope import (
    KG_AUXILIARY_MODE,
    build_official_doc_kg_ingest_plan,
)


def test_official_markdown_upload_builds_client_rag_auxiliary_plan() -> None:
    plan = build_official_doc_kg_ingest_plan(
        {
            "ingestion_id": "ing-1",
            "knowledge_type": "official",
            "source_type": "official_markdown_upload",
            "document_id": "official-doc-1",
            "title": "Token auth",
            "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
        }
    )

    assert plan is not None
    assert plan.mode == KG_AUXILIARY_MODE
    assert plan.knowledge_type == "official"
    assert plan.source_type == "official_markdown_upload"
    assert plan.document_id == "official-doc-1"
    assert plan.source_url == "https://docs.agora.io/en/video-calling/token-authentication"
    assert plan.provenance_required is True
    assert plan.allow_answer_without_rag_citation is False
    assert plan.includes_confirmed_case_memory is False


def test_technical_articles_are_out_of_first_phase_kg_scope() -> None:
    plan = build_official_doc_kg_ingest_plan(
        {
            "ingestion_id": "tech-1",
            "knowledge_type": "technical",
            "source_type": "technical_article_api",
            "document_id": "case-doc-1",
            "title": "Customer troubleshooting case",
        }
    )

    assert plan is None


def test_external_benchmarks_are_not_treated_as_official_kg_docs() -> None:
    plan = build_official_doc_kg_ingest_plan(
        {
            "ingestion_id": "bench-1",
            "knowledge_type": "official",
            "source_type": "external_benchmark",
            "document_id": "external-benchmark-placeholder",
        }
    )

    assert plan is None


def test_confirmed_case_memory_markers_are_rejected_even_with_official_fields() -> None:
    plan = build_official_doc_kg_ingest_plan(
        {
            "ingestion_id": "case-1",
            "knowledge_type": "official",
            "source_type": "official_markdown_upload",
            "document_id": "case-memory-doc",
            "case_memory_ledger_id": "ledger-1",
            "memory_type": "confirmed_case",
            "request_metadata": {"case_memory_status": "active"},
        }
    )

    assert plan is None


def test_unknown_or_missing_source_type_does_not_default_into_kg_scope() -> None:
    assert build_official_doc_kg_ingest_plan({"knowledge_type": "official"}) is None
    assert (
        build_official_doc_kg_ingest_plan(
            {"knowledge_type": "official", "source_type": "confirmed_case_memory"}
        )
        is None
    )

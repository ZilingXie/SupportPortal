from backend.services.rag_access_policy import (
    OFFICIAL_KNOWLEDGE_TYPE,
    OFFICIAL_SOURCE_TYPE,
    RAG_ACCESS_NON_OFFICIAL_ONLY,
    RAG_ACCESS_OFFICIAL_ONLY,
    is_official_metadata,
    metadata_filter_for_access_mode,
    normalize_rag_access_mode,
)


def test_official_access_filter_uses_existing_official_fields() -> None:
    policy = metadata_filter_for_access_mode(RAG_ACCESS_OFFICIAL_ONLY)

    assert policy.include == {
        "knowledge_type": OFFICIAL_KNOWLEDGE_TYPE,
        "source_type": OFFICIAL_SOURCE_TYPE,
    }
    assert policy.exclude == {}


def test_non_official_access_filter_excludes_official_source_and_type() -> None:
    policy = metadata_filter_for_access_mode(RAG_ACCESS_NON_OFFICIAL_ONLY)

    assert policy.include == {}
    assert policy.exclude == {
        "knowledge_type": OFFICIAL_KNOWLEDGE_TYPE,
        "source_type": OFFICIAL_SOURCE_TYPE,
    }


def test_access_mode_normalization_accepts_only_internal_runtime_modes() -> None:
    assert normalize_rag_access_mode("official_only") == RAG_ACCESS_OFFICIAL_ONLY
    assert normalize_rag_access_mode("non-official-only") == RAG_ACCESS_NON_OFFICIAL_ONLY
    assert normalize_rag_access_mode("bad-value") is None
    assert normalize_rag_access_mode("") is None
    assert normalize_rag_access_mode(None) is None


def test_official_metadata_requires_official_knowledge_type_and_source_type() -> None:
    assert is_official_metadata(
        {
            "knowledge_type": "official",
            "source_type": "official_markdown_upload",
        }
    )
    assert not is_official_metadata(
        {
            "knowledge_type": "official",
            "source_type": "external_benchmark",
        }
    )
    assert not is_official_metadata(
        {
            "knowledge_type": "technical",
            "source_type": "technical_article_api",
        }
    )

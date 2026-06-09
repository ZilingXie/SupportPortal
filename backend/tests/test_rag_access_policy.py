from backend.services.rag_access_policy import (
    CLIENT_EXTERNAL_ONLY,
    ENGINEER_EXTERNAL_FALLBACK,
    ENGINEER_INTERNAL_FIRST,
    EXTERNAL_SCOPE,
    INTERNAL_SCOPE,
    normalize_knowledge_scope,
    normalize_retrieval_policy,
    scope_for_knowledge_type,
    scope_for_retrieval_policy,
)


def test_official_knowledge_maps_to_external_scope() -> None:
    assert scope_for_knowledge_type("official") == EXTERNAL_SCOPE


def test_non_official_knowledge_fails_closed_to_internal_scope() -> None:
    assert scope_for_knowledge_type("technical") == INTERNAL_SCOPE
    assert scope_for_knowledge_type("runbook") == INTERNAL_SCOPE
    assert scope_for_knowledge_type("") == INTERNAL_SCOPE
    assert scope_for_knowledge_type(None) == INTERNAL_SCOPE


def test_scope_normalization_accepts_external_and_internal_aliases() -> None:
    assert normalize_knowledge_scope("external") == EXTERNAL_SCOPE
    assert normalize_knowledge_scope("PUBLIC") == EXTERNAL_SCOPE
    assert normalize_knowledge_scope("client-safe") == EXTERNAL_SCOPE
    assert normalize_knowledge_scope("internal") == INTERNAL_SCOPE
    assert normalize_knowledge_scope("private") == INTERNAL_SCOPE
    assert normalize_knowledge_scope("engineer_only") == INTERNAL_SCOPE


def test_invalid_scope_defaults_to_internal() -> None:
    assert normalize_knowledge_scope("bad-value") == INTERNAL_SCOPE
    assert normalize_knowledge_scope("") == INTERNAL_SCOPE
    assert normalize_knowledge_scope(None) == INTERNAL_SCOPE


def test_retrieval_policy_normalization_accepts_supported_values_and_aliases() -> None:
    assert normalize_retrieval_policy("client_external_only") == CLIENT_EXTERNAL_ONLY
    assert normalize_retrieval_policy("client-external-only") == CLIENT_EXTERNAL_ONLY
    assert normalize_retrieval_policy("ENGINEER_INTERNAL_FIRST") == ENGINEER_INTERNAL_FIRST
    assert normalize_retrieval_policy("engineer-external-fallback") == ENGINEER_EXTERNAL_FALLBACK


def test_invalid_retrieval_policy_defaults_to_client_external_only() -> None:
    assert normalize_retrieval_policy("bad-value") == CLIENT_EXTERNAL_ONLY
    assert normalize_retrieval_policy("") == CLIENT_EXTERNAL_ONLY
    assert normalize_retrieval_policy(None) == CLIENT_EXTERNAL_ONLY


def test_policy_maps_to_single_scope_when_policy_is_single_scope() -> None:
    assert scope_for_retrieval_policy(CLIENT_EXTERNAL_ONLY) == EXTERNAL_SCOPE
    assert scope_for_retrieval_policy(ENGINEER_EXTERNAL_FALLBACK) == EXTERNAL_SCOPE
    assert scope_for_retrieval_policy(ENGINEER_INTERNAL_FIRST) == INTERNAL_SCOPE

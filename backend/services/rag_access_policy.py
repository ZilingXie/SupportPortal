from __future__ import annotations

from typing import Any

EXTERNAL_SCOPE = "external"
INTERNAL_SCOPE = "internal"

CLIENT_EXTERNAL_ONLY = "client_external_only"
ENGINEER_INTERNAL_FIRST = "engineer_internal_first"
ENGINEER_EXTERNAL_FALLBACK = "engineer_external_fallback"

_EXTERNAL_SCOPE_ALIASES = {
    "client_safe",
    "client-safe",
    "external",
    "official",
    "public",
}
_INTERNAL_SCOPE_ALIASES = {
    "engineer_only",
    "engineer-only",
    "internal",
    "private",
    "technical",
}

_RETRIEVAL_POLICY_ALIASES = {
    CLIENT_EXTERNAL_ONLY: CLIENT_EXTERNAL_ONLY,
    "client-external-only": CLIENT_EXTERNAL_ONLY,
    "client_external": CLIENT_EXTERNAL_ONLY,
    "client-external": CLIENT_EXTERNAL_ONLY,
    ENGINEER_INTERNAL_FIRST: ENGINEER_INTERNAL_FIRST,
    "engineer-internal-first": ENGINEER_INTERNAL_FIRST,
    "internal_first": ENGINEER_INTERNAL_FIRST,
    "internal-first": ENGINEER_INTERNAL_FIRST,
    ENGINEER_EXTERNAL_FALLBACK: ENGINEER_EXTERNAL_FALLBACK,
    "engineer-external-fallback": ENGINEER_EXTERNAL_FALLBACK,
    "external_fallback": ENGINEER_EXTERNAL_FALLBACK,
    "external-fallback": ENGINEER_EXTERNAL_FALLBACK,
}


def _normalized_token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def normalize_knowledge_scope(value: Any) -> str:
    normalized = _normalized_token(value)
    if normalized in _EXTERNAL_SCOPE_ALIASES:
        return EXTERNAL_SCOPE
    if normalized in _INTERNAL_SCOPE_ALIASES:
        return INTERNAL_SCOPE
    return INTERNAL_SCOPE


def scope_for_knowledge_type(knowledge_type: Any) -> str:
    normalized = _normalized_token(knowledge_type).replace("-", "_")
    if normalized == "official":
        return EXTERNAL_SCOPE
    return INTERNAL_SCOPE


def normalize_retrieval_policy(value: Any) -> str:
    normalized = _normalized_token(value)
    return _RETRIEVAL_POLICY_ALIASES.get(normalized, CLIENT_EXTERNAL_ONLY)


def scope_for_retrieval_policy(policy: Any) -> str:
    normalized = normalize_retrieval_policy(policy)
    if normalized in {CLIENT_EXTERNAL_ONLY, ENGINEER_EXTERNAL_FALLBACK}:
        return EXTERNAL_SCOPE
    return INTERNAL_SCOPE

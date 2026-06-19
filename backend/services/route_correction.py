from __future__ import annotations

from typing import Any

VALID_ROUTE_TUPLES: list[dict[str, str]] = [
    {
        "scope_label": "ticket_resolution",
        "execution_action": "resolve_ticket",
        "route_family": "ticket_resolution",
        "tooling_profile": "deterministic_resolution",
    },
    {
        "scope_label": "billing",
        "execution_action": "account_suspension",
        "route_family": "billing_automation",
        "tooling_profile": "deterministic_billing_intake",
    },
    {
        "scope_label": "billing",
        "execution_action": "detailed_invoice",
        "route_family": "billing_automation",
        "tooling_profile": "deterministic_billing_intake",
    },
    {
        "scope_label": "billing",
        "execution_action": "account_verification",
        "route_family": "billing_automation",
        "tooling_profile": "deterministic_billing_intake",
    },
    {
        "scope_label": "billing",
        "execution_action": "human_review_required",
        "route_family": "billing_review",
        "tooling_profile": "deterministic_billing_intake",
    },
    {
        "scope_label": "billing",
        "execution_action": "refuse",
        "route_family": "fallback_or_refuse",
        "tooling_profile": "no_agora_docs_refusal",
    },
    {
        "scope_label": "agora_technical",
        "execution_action": "rag",
        "route_family": "agora_docs_rag",
        "tooling_profile": "agora_docs_only",
    },
    {
        "scope_label": "agora_non_technical",
        "execution_action": "web_search",
        "route_family": "web_company_info",
        "tooling_profile": "official_web_search",
    },
    {
        "scope_label": "agora_non_technical",
        "execution_action": "refuse",
        "route_family": "web_company_info",
        "tooling_profile": "no_agora_docs_refusal",
    },
    {
        "scope_label": "small_talk",
        "execution_action": "controlled_response",
        "route_family": "general_chat",
        "tooling_profile": "controlled_acknowledgement",
    },
    {
        "scope_label": "small_talk",
        "execution_action": "refuse",
        "route_family": "general_chat",
        "tooling_profile": "no_agora_docs_refusal",
    },
    {
        "scope_label": "non_agora",
        "execution_action": "refuse",
        "route_family": "fallback_or_refuse",
        "tooling_profile": "no_agora_docs_refusal",
    },
]

_TUPLE_INDEX: dict[tuple[str, str], dict[str, str]] = {
    (item["scope_label"], item["execution_action"]): item for item in VALID_ROUTE_TUPLES
}
_VALID_SCOPES = {item["scope_label"] for item in VALID_ROUTE_TUPLES}


class RouteCorrectionValidationError(ValueError):
    pass


def validate_route_correction(
    *,
    scope_label: str,
    execution_action: str,
    note: str | None = None,
) -> dict[str, Any]:
    normalized_scope = _normalize(scope_label)
    normalized_action = _normalize(execution_action)
    match = _TUPLE_INDEX.get((normalized_scope, normalized_action))
    if match is None:
        if normalized_scope not in _VALID_SCOPES:
            raise RouteCorrectionValidationError(f"invalid scope_label: {scope_label!r}")
        raise RouteCorrectionValidationError(
            f"invalid execution_action {execution_action!r} for scope_label {normalized_scope!r}"
        )
    return {
        "scope_label": match["scope_label"],
        "execution_action": match["execution_action"],
        "route_family": match["route_family"],
        "tooling_profile": match["tooling_profile"],
        "note": " ".join(str(note or "").split()).strip(),
    }


def _normalize(value: str) -> str:
    return " ".join(str(value or "").split()).strip().lower()

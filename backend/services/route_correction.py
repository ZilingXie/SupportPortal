from __future__ import annotations

from typing import Any

from backend.services.automation_routing import AUTOMATED_ROUTE_FAMILY, automation_metadata

VALID_ROUTE_TUPLES: list[dict[str, str]] = [
    {
        "scope_label": "conversation",
        "execution_action": "follow_up",
        "route_family": "conversation",
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "conversation",
        "execution_action": "human_review_required",
        "route_family": "human_review",
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "human_review",
        "execution_action": "human_review_required",
        "route_family": "human_review",
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "uncertain",
        "execution_action": "human_review_required",
        "route_family": "human_review",
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "uncategorized",
        "execution_action": "human_review_required",
        "route_family": "human_review",
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "account_billing",
        "execution_action": "human_review_required",
        "route_family": "human_review",
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "automation",
        "execution_action": "unregistered",
        "route_family": "human_review",
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "unclear",
        "execution_action": "human_review_required",
        "route_family": "human_review",
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "non_agora",
        "execution_action": "human_review_required",
        "route_family": "human_review",
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "ticket_resolution",
        "execution_action": "resolve_ticket",
        "route_family": "ticket_resolution",
        "tooling_profile": "deterministic_resolution",
    },
    {
        "scope_label": "fraud_account",
        "execution_action": "fraud_account",
        "route_family": AUTOMATED_ROUTE_FAMILY,
        "tooling_profile": "deterministic_billing_intake",
    },
    {
        "scope_label": "billing",
        "execution_action": "account_verification",
        "route_family": AUTOMATED_ROUTE_FAMILY,
        "tooling_profile": "deterministic_billing_intake",
    },
    {
        "scope_label": "account_suspension",
        "execution_action": "account_suspension",
        "route_family": AUTOMATED_ROUTE_FAMILY,
        "tooling_profile": "classification_only",
    },
    {
        "scope_label": "billing",
        "execution_action": "detailed_invoice",
        "route_family": AUTOMATED_ROUTE_FAMILY,
        "tooling_profile": "deterministic_billing_intake",
    },
    {
        "scope_label": "enablement",
        "execution_action": "enablement",
        "route_family": AUTOMATED_ROUTE_FAMILY,
        "tooling_profile": "deterministic_enablement_intake",
    },
    {
        "scope_label": "quota",
        "execution_action": "quota",
        "route_family": AUTOMATED_ROUTE_FAMILY,
        "tooling_profile": "deterministic_quota_intake",
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
    metadata = automation_metadata(
        route_family=match["route_family"],
        execution_action=match["execution_action"],
    )
    return {
        "scope_label": match["scope_label"],
        "execution_action": match["execution_action"],
        "route_family": match["route_family"],
        "tooling_profile": match["tooling_profile"],
        **metadata,
    }


def _normalize(value: str) -> str:
    return " ".join(str(value or "").split()).strip().lower()

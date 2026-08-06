"""Canonical route taxonomy used by the Account case list and filters."""

from __future__ import annotations

from typing import Any

from backend.services.automation_routing import canonical_automation_subcategory


ACCOUNT_CASE_FILTER_GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "all", "label": "All", "children": ()},
    {
        "id": "automation",
        "label": "Automation",
        "children": (
            {"id": "fraud_account", "label": "Fraud Account"},
            {"id": "detailed_invoice", "label": "Detailed Invoice"},
            {"id": "enablement", "label": "Enablement"},
            {"id": "quota", "label": "Quota"},
            {"id": "unregistered", "label": "Unregistered"},
        ),
    },
    {
        "id": "account_billing",
        "label": "Account & Billing",
        "children": (
            {"id": "account_suspension", "label": "Account Suspension"},
            {"id": "other", "label": "Other"},
        ),
    },
    {"id": "agora_technical", "label": "Tech", "children": ()},
    {"id": "agora_non_technical", "label": "Non-tech", "children": ()},
    {
        "id": "conversation",
        "label": "Conversation",
        "children": (
            {"id": "resolve", "label": "Resolve"},
            {"id": "follow_up", "label": "Follow-up"},
            {"id": "human_review", "label": "Human Review"},
        ),
    },
    {
        "id": "human_review",
        "label": "Human Review",
        "children": (
            {"id": "uncategorized", "label": "Uncategorized"},
            {"id": "uncertain", "label": "Uncertain"},
            {"id": "non_agora", "label": "Non-Agora"},
            {"id": "other", "label": "Other"},
        ),
    },
)

_GROUPS_BY_ID = {group["id"]: group for group in ACCOUNT_CASE_FILTER_GROUPS}
_CHILDREN_BY_GROUP = {
    group_id: {child["id"]: child for child in group["children"]}
    for group_id, group in _GROUPS_BY_ID.items()
}
_LEGACY_ROUTE_FILTERS = {
    "human_review": "human_review",
    "conversation": "conversation",
    "agora_technical": "agora_technical",
    "agora_non_technical": "agora_non_technical",
    "account_billing": "account_billing",
    "uncertain": "human_review:uncertain",
}
_AUTOMATION_SUBCATEGORIES = {
    "account_verification": "fraud_account",
    "fraud_account": "fraud_account",
    "detailed_invoice": "detailed_invoice",
    "enablement": "enablement",
    "quota": "quota",
    "unregistered": "unregistered",
}


def account_case_filter_definitions() -> list[dict[str, Any]]:
    """Return JSON-safe taxonomy metadata without exposing mutable module state."""
    return [
        {
            "id": group["id"],
            "label": group["label"],
            "children": [dict(child) for child in group["children"]],
        }
        for group in ACCOUNT_CASE_FILTER_GROUPS
    ]


def normalize_account_case_filter(
    *,
    group: str | None = None,
    subcategory: str | None = None,
    legacy_label: str | None = None,
) -> str | None:
    """Normalize new group/leaf and legacy route_label values to one filter key."""
    normalized_group = str(group or "").strip().lower()
    normalized_subcategory = str(subcategory or "").strip().lower()
    normalized_legacy = str(legacy_label or "").strip().lower()
    if normalized_group and normalized_legacy:
        raise ValueError("route_group and route_label cannot be used together")
    if normalized_subcategory and not normalized_group:
        raise ValueError("route_subcategory requires route_group")
    if normalized_group:
        if normalized_group == "all":
            if normalized_subcategory:
                raise ValueError("All does not accept a route_subcategory")
            return None
        if normalized_group not in _GROUPS_BY_ID:
            raise ValueError("unsupported route_group")
        children = _CHILDREN_BY_GROUP[normalized_group]
        if normalized_subcategory:
            if normalized_subcategory not in children:
                raise ValueError("unsupported route_subcategory for route_group")
            return f"{normalized_group}:{normalized_subcategory}"
        return normalized_group
    if normalized_legacy:
        if normalized_legacy in _LEGACY_ROUTE_FILTERS:
            return _LEGACY_ROUTE_FILTERS[normalized_legacy]
        if normalized_legacy == "all":
            return None
        if normalized_legacy in _GROUPS_BY_ID:
            return normalized_legacy
        if ":" in normalized_legacy:
            group_id, child_id = normalized_legacy.split(":", 1)
            return normalize_account_case_filter(group=group_id, subcategory=child_id)
        raise ValueError("unsupported route_label")
    return None


def _automation_leaf(item: dict[str, Any]) -> str | None:
    classification = item.get("route_classification")
    candidate = (
        classification.get("automation_subcategory")
        if isinstance(classification, dict)
        else None
    )
    candidate = canonical_automation_subcategory(
        candidate or item.get("subcategory") or item.get("execution_action") or item.get("route")
    )
    return _AUTOMATION_SUBCATEGORIES.get(candidate)


def account_case_filter_key(item: dict[str, Any]) -> str:
    """Map an Account case to exactly one stable group or group:leaf key."""
    classification = item.get("route_classification")
    if isinstance(classification, dict) and classification:
        intent = str(classification.get("intent_class") or "unclear").strip().lower()
        if intent == "conversation":
            action = str(classification.get("conversation_action") or "human_review").strip().lower()
            return f"conversation:{action if action in {'resolve', 'follow_up'} else 'human_review'}"
        if intent == "uncertain":
            return "human_review:uncertain"
        if intent == "agora":
            agora_route = str(classification.get("agora_route") or "uncategorized").strip().lower()
            if agora_route == "technical":
                return "agora_technical"
            if agora_route == "non_technical":
                return "agora_non_technical"
            if agora_route == "account_billing":
                subcategory = str(
                    classification.get("account_billing_subcategory") or item.get("subcategory") or "other"
                ).strip().lower()
                return f"account_billing:{subcategory if subcategory == 'account_suspension' else 'other'}"
            if agora_route == "automation":
                return f"automation:{_automation_leaf(item) or 'unregistered'}"
            return "human_review:uncategorized"
        if intent == "support_request":
            support_scope = str(classification.get("support_scope") or "unclear").strip().lower()
            agora_route = str(classification.get("agora_route") or "unclear").strip().lower()
            if support_scope == "non_agora":
                return "human_review:non_agora"
            if agora_route == "technical":
                return "agora_technical"
            if agora_route == "non_technical":
                return "agora_non_technical"
            if agora_route == "account_billing":
                subcategory = str(classification.get("account_billing_subcategory") or "other").strip().lower()
                return f"account_billing:{subcategory if subcategory == 'account_suspension' else 'other'}"
            if agora_route == "automation":
                return f"automation:{_automation_leaf(item) or 'unregistered'}"
            return "human_review:other"
        return "human_review:uncertain"

    action = canonical_automation_subcategory(item.get("execution_action") or item.get("route"))
    if action == "account_suspension":
        return "account_billing:account_suspension"
    if str(item.get("route_family") or "").strip().lower() in {"automated", "billing_automation"}:
        return f"automation:{_automation_leaf(item) or 'unregistered'}"

    scope = str(item.get("scope_label") or "").strip().lower()
    if scope == "ticket_resolution":
        return "conversation:resolve"
    if scope in {"small_talk", "conversation"}:
        return "conversation:follow_up"
    if scope == "agora_technical":
        return "agora_technical"
    if scope == "agora_non_technical":
        return "agora_non_technical"
    if scope in {"account_billing", "billing"}:
        subcategory = str(item.get("subcategory") or "other").strip().lower()
        return f"account_billing:{subcategory if subcategory == 'account_suspension' else 'other'}"
    if scope in {"uncertain", "unclear"}:
        return "human_review:uncertain"
    if scope == "non_agora":
        return "human_review:non_agora"
    if scope in {"human_review", "uncategorized"}:
        return "human_review:uncategorized"
    return "human_review:other"


def account_case_filter_matches(item: dict[str, Any], filter_key: str | None) -> bool:
    if not filter_key:
        return True
    actual = account_case_filter_key(item)
    return actual == filter_key or actual.split(":", 1)[0] == filter_key


def account_case_filter_keys() -> tuple[str, ...]:
    keys = ["all"]
    for group in ACCOUNT_CASE_FILTER_GROUPS:
        group_id = group["id"]
        if group_id == "all":
            continue
        keys.append(group_id)
        keys.extend(f"{group_id}:{child['id']}" for child in group["children"])
    return tuple(keys)

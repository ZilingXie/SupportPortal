"""Canonical route taxonomy used by the Account case list and filters."""

from __future__ import annotations

from typing import Any

from backend.services.automation_routing import canonical_automation_subcategory


ACCOUNT_CASE_FILTER_GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "all", "label": "All", "children": ()},
    {
        "id": "automation",
        "label": "Automated",
        "children": (
            {"id": "fraud_account", "label": "Account & Billing / Fraud Account"},
            {"id": "enablement", "label": "Backend Operation / Enablement"},
        ),
    },
    {
        "id": "backend_operation",
        "label": "Backend Operation",
        "children": (
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
            {"id": "fraud_account", "label": "Fraud Account"},
            {"id": "detailed_invoice", "label": "Detailed Invoice"},
            {"id": "other", "label": "Other"},
        ),
    },
    {"id": "agora_technical", "label": "Tech", "children": ()},
    {"id": "security_compliance", "label": "Security & Compliance", "children": ()},
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
_DEPRECATED_GROUP_FILTERS = {"agora_non_technical"}
_BACKEND_OPERATION_SUBCATEGORIES = {"enablement", "quota", "unregistered"}
_AUTOMATION_SUBCATEGORIES = {
    "fraud_account",
    "enablement",
}
_ACCOUNT_BILLING_SUBCATEGORIES = {
    "account_suspension",
    "fraud_account",
    "detailed_invoice",
    "other",
}
_HUMAN_REVIEW_SUBCATEGORIES = {
    "uncategorized",
    "uncertain",
    "non_agora",
    "other",
}
_LEGACY_HUMAN_REVIEW_UNREGISTERED = "human_review:unregistered"


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
        # This leaf was removed from the canonical Human Review taxonomy, but
        # retain its old query semantics for clients that have not migrated.
        if (
            normalized_group == "human_review"
            and normalized_subcategory == "unregistered"
        ):
            return _LEGACY_HUMAN_REVIEW_UNREGISTERED
        if normalized_group in _DEPRECATED_GROUP_FILTERS:
            if normalized_subcategory:
                raise ValueError("deprecated route_group does not accept a route_subcategory")
            return normalized_group
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
            if normalized_legacy == _LEGACY_HUMAN_REVIEW_UNREGISTERED:
                return _LEGACY_HUMAN_REVIEW_UNREGISTERED
            return normalize_account_case_filter(group=group_id, subcategory=child_id)
        raise ValueError("unsupported route_label")
    return None


def backend_operation_metadata(subcategory: Any) -> dict[str, str | None]:
    normalized = str(subcategory or "").strip().lower()
    if normalized not in _BACKEND_OPERATION_SUBCATEGORIES:
        normalized = "unregistered"
    automated = normalized == "enablement"
    return {
        "category": "backend_operation",
        "subcategory": normalized,
        "route_status": "automated" if automated else "not_automated",
        "automation_handler": normalized if automated else None,
    }


def security_compliance_metadata() -> dict[str, str | None]:
    """Return the classification-only metadata for Security & Compliance."""
    return {
        "category": "security_compliance",
        "subcategory": None,
        "route_status": "not_automated",
        "automation_handler": None,
    }


def _first_nonempty_route_value(*values: Any) -> str:
    for value in values:
        normalized = str(value or "").strip().lower()
        if normalized:
            return normalized
    return ""


def _account_billing_leaf(item: dict[str, Any], classification: dict[str, Any] | None = None) -> str:
    source = classification if isinstance(classification, dict) else item.get("route_classification")
    candidate = _first_nonempty_route_value(
        source.get("account_billing_subcategory") if isinstance(source, dict) else None,
        item.get("subcategory"),
    ) or "other"
    if candidate == "account_verification":
        candidate = "fraud_account"
    return candidate if candidate in _ACCOUNT_BILLING_SUBCATEGORIES else "other"


def _backend_operation_leaf(item: dict[str, Any], classification: dict[str, Any] | None = None) -> str:
    source = classification if isinstance(classification, dict) else item.get("route_classification")
    candidate = _first_nonempty_route_value(
        source.get("backend_operation_subcategory") if isinstance(source, dict) else None,
        source.get("automation_subcategory") if isinstance(source, dict) else None,
        item.get("subcategory"),
        item.get("execution_action"),
        item.get("route"),
    )
    return candidate if candidate in {"enablement", "quota", "unregistered"} else "unregistered"


def _automation_leaf(item: dict[str, Any], classification: dict[str, Any] | None = None) -> str:
    source = classification if isinstance(classification, dict) else item.get("route_classification")
    candidate = _first_nonempty_route_value(
        source.get("automation_subcategory") if isinstance(source, dict) else None,
        item.get("subcategory"),
        item.get("execution_action"),
        item.get("route"),
    )
    if candidate in {"account_verification", "fraud_account", "detailed_invoice"}:
        return candidate
    if candidate in _BACKEND_OPERATION_SUBCATEGORIES:
        return candidate
    return "unregistered"


def account_case_filter_key(item: dict[str, Any]) -> str:
    """Map an Account case to its canonical primary group/leaf key.

    The primary key is deliberately separate from filter membership: registered
    billing automations retain their Account & Billing label while also appearing
    in the Automation filter.
    """
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
            if agora_route == "security_compliance":
                return "security_compliance"
            if agora_route == "account_billing":
                return f"account_billing:{_account_billing_leaf(item, classification)}"
            if agora_route == "backend_operation":
                leaf = _backend_operation_leaf(item, classification)
                return f"backend_operation:{leaf}"
            if agora_route == "automation":
                candidate = _automation_leaf(item, classification)
                if candidate in {"fraud_account", "account_verification", "detailed_invoice"}:
                    return f"account_billing:{'fraud_account' if candidate == 'account_verification' else candidate}"
                if candidate in _BACKEND_OPERATION_SUBCATEGORIES:
                    return f"backend_operation:{candidate}"
                return "backend_operation:unregistered"
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
            if agora_route == "security_compliance":
                return "security_compliance"
            if agora_route == "account_billing":
                return f"account_billing:{_account_billing_leaf(item, classification)}"
            if agora_route == "backend_operation":
                leaf = _backend_operation_leaf(item, classification)
                return f"backend_operation:{leaf}"
            if agora_route == "automation":
                candidate = _automation_leaf(item, classification)
                if candidate in {"fraud_account", "account_verification", "detailed_invoice"}:
                    return f"account_billing:{'fraud_account' if candidate == 'account_verification' else candidate}"
                if candidate in _BACKEND_OPERATION_SUBCATEGORIES:
                    return f"backend_operation:{candidate}"
                return "backend_operation:unregistered"
            return "human_review:uncategorized"
        return "human_review:uncertain"

    action = canonical_automation_subcategory(
        item.get("execution_action") or item.get("subcategory") or item.get("route")
    )
    if action == "account_suspension":
        return "account_billing:account_suspension"
    if str(item.get("route_family") or "").strip().lower() in {"automated", "billing_automation"}:
        if action in {"fraud_account", "account_verification", "detailed_invoice"}:
            return f"account_billing:{'fraud_account' if action == 'account_verification' else action}"
        if action in _BACKEND_OPERATION_SUBCATEGORIES:
            return f"backend_operation:{action}"
        return "backend_operation:unregistered"

    scope = str(item.get("scope_label") or "").strip().lower()
    if scope == "ticket_resolution":
        return "conversation:resolve"
    if scope in {"small_talk", "conversation"}:
        return "conversation:follow_up"
    if scope == "agora_technical":
        return "agora_technical"
    if scope == "agora_non_technical":
        return "agora_non_technical"
    if scope in {"security_compliance", "agora_security_compliance"}:
        return "security_compliance"
    if scope in {"account_billing", "billing"}:
        return f"account_billing:{_account_billing_leaf(item)}"
    if scope in {"automation", "backend_operation", "enablement", "quota"}:
        if action in _BACKEND_OPERATION_SUBCATEGORIES:
            return f"backend_operation:{action}"
        return "backend_operation:unregistered"
    if scope in {"unregistered"}:
        return "backend_operation:unregistered"
    if scope in {"uncertain", "unclear"}:
        return "human_review:uncertain"
    if scope == "non_agora":
        return "human_review:non_agora"
    if scope in {"human_review", "uncategorized"}:
        return "human_review:uncategorized"
    return "human_review:other"


def account_case_filter_memberships(item: dict[str, Any]) -> frozenset[str]:
    """Return all filter keys that should include an Account case.

    Registered automation is intentionally represented in both its business
    group and the cross-business execution view. Human Review is strict: only
    canonical ``human_review:*`` primary keys receive that membership.
    """
    primary = account_case_filter_key(item)
    memberships = {primary}
    if ":" in primary:
        memberships.add(primary.split(":", 1)[0])
    route_status = str(item.get("route_status") or "").strip().lower()
    route_family = str(item.get("route_family") or "").strip().lower()
    automation_child_by_primary = {
        "account_billing:fraud_account": "fraud_account",
        "account_billing:detailed_invoice": "detailed_invoice",
        "backend_operation:enablement": "enablement",
        "backend_operation:quota": "quota",
    }
    if (
        (route_status == "automated" or route_family in {"automated", "billing_automation"})
        and primary in automation_child_by_primary
    ):
        automation_child = automation_child_by_primary[primary]
        memberships.add("automation")
        memberships.add(f"automation:{automation_child}")
    if primary.startswith("human_review:"):
        memberships.add("human_review")
    return frozenset(memberships)


def account_case_filter_matches(item: dict[str, Any], filter_key: str | None) -> bool:
    if not filter_key:
        return True
    if filter_key == _LEGACY_HUMAN_REVIEW_UNREGISTERED:
        # Deprecated compatibility only. This intentionally does not appear
        # in canonical memberships, definitions, or facet counts.
        return account_case_filter_key(item) == "backend_operation:unregistered"
    return filter_key in account_case_filter_memberships(item)


def account_case_filter_keys() -> tuple[str, ...]:
    keys = ["all"]
    for group in ACCOUNT_CASE_FILTER_GROUPS:
        group_id = group["id"]
        if group_id == "all":
            continue
        keys.append(group_id)
        keys.extend(f"{group_id}:{child['id']}" for child in group["children"])
    return tuple(keys)

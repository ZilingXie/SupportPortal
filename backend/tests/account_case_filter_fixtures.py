"""Shared Python/PostgreSQL fixtures for Account filter membership parity."""

from __future__ import annotations

from typing import Any


ACCOUNT_CASE_FILTER_PARITY_FIXTURES: tuple[
    tuple[str, dict[str, Any], frozenset[str]], ...
] = (
    (
        "agora_technical",
        {"route_classification": {"intent_class": "agora", "agora_route": "technical"}},
        frozenset({"agora_technical"}),
    ),
    (
        "security_compliance",
        {
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "security_compliance",
            }
        },
        frozenset({"security_compliance"}),
    ),
    (
        "automated_fraud_account",
        {
            "route_status": "automated",
            "route_family": "automated",
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "account_billing",
                "account_billing_subcategory": "fraud_account",
            },
        },
        frozenset(
            {
                "account_billing:fraud_account",
                "account_billing",
                "automation",
                "automation:fraud_account",
            }
        ),
    ),
    (
        "automated_detailed_invoice",
        {
            "route_status": "automated",
            "route_family": "automated",
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "account_billing",
                "account_billing_subcategory": "detailed_invoice",
            },
        },
        frozenset(
            {
                "account_billing:detailed_invoice",
                "account_billing",
            }
        ),
    ),
    (
        "account_suspension",
        {
            "route_status": "not_automated",
            "route_family": "human_review",
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "account_billing",
                "account_billing_subcategory": "account_suspension",
            },
        },
        frozenset({"account_billing:account_suspension", "account_billing"}),
    ),
    (
        "account_billing_other",
        {
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "account_billing",
                "account_billing_subcategory": "other",
            }
        },
        frozenset({"account_billing:other", "account_billing"}),
    ),
    (
        "automated_enablement",
        {
            "route_status": "automated",
            "route_family": "automated",
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "backend_operation",
                "backend_operation_subcategory": "enablement",
            },
        },
        frozenset(
            {
                "backend_operation:enablement",
                "backend_operation",
                "automation",
                "automation:enablement",
            }
        ),
    ),
    (
        "automated_quota",
        {
            "route_status": "automated",
            "route_family": "automated",
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "backend_operation",
                "backend_operation_subcategory": "quota",
            },
        },
        frozenset(
            {
                "backend_operation:quota",
                "backend_operation",
                "automation",
                "automation:quota",
            }
        ),
    ),
    (
        "backend_operation_unregistered",
        {
            "route_status": "not_automated",
            "route_family": "human_review",
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "backend_operation",
                "backend_operation_subcategory": "unregistered",
            },
        },
        frozenset({"backend_operation:unregistered", "backend_operation"}),
    ),
    (
        "conversation_resolve",
        {
            "route_classification": {
                "intent_class": "conversation",
                "conversation_action": "resolve",
            }
        },
        frozenset({"conversation:resolve", "conversation"}),
    ),
    (
        "conversation_human_review",
        {
            "route_classification": {
                "intent_class": "conversation",
                "conversation_action": "human_review",
            }
        },
        frozenset({"conversation:human_review", "conversation"}),
    ),
    (
        "uncertain",
        {"route_classification": {"intent_class": "uncertain"}},
        frozenset({"human_review:uncertain", "human_review"}),
    ),
    (
        "support_request_account_billing_row_fallback",
        {
            "route_classification": {
                "intent_class": "support_request",
                "support_scope": "agora",
                "agora_route": "account_billing",
            },
            "subcategory": "detailed_invoice",
        },
        frozenset({"account_billing:detailed_invoice", "account_billing"}),
    ),
    (
        "support_request_automation_row_fallback",
        {
            "route_status": "automated",
            "route_family": "automated",
            "route_classification": {
                "intent_class": "support_request",
                "support_scope": "agora",
                "agora_route": "automation",
            },
            "subcategory": "enablement",
        },
        frozenset(
            {
                "backend_operation:enablement",
                "backend_operation",
                "automation",
                "automation:enablement",
            }
        ),
    ),
    (
        "support_request_uncategorized",
        {
            "route_classification": {
                "intent_class": "support_request",
                "support_scope": "agora",
                "agora_route": "unclear",
            }
        },
        frozenset({"human_review:uncategorized", "human_review"}),
    ),
    (
        "legacy_billing_automation",
        {
            "route_family": "billing_automation",
            "subcategory": "detailed_invoice",
        },
        frozenset(
            {
                "account_billing:detailed_invoice",
                "account_billing",
            }
        ),
    ),
    (
        "blank_classification_falls_back_to_row",
        {
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "account_billing",
                "account_billing_subcategory": "   ",
            },
            "subcategory": "fraud_account",
        },
        frozenset({"account_billing:fraud_account", "account_billing"}),
    ),
    (
        "blank_backend_classification_falls_back_to_row",
        {
            "route_classification": {
                "intent_class": "support_request",
                "support_scope": "agora",
                "agora_route": "backend_operation",
                "backend_operation_subcategory": "",
                "automation_subcategory": " ",
            },
            "subcategory": "quota",
        },
        frozenset({"backend_operation:quota", "backend_operation"}),
    ),
    (
        "blank_automation_classification_falls_back_to_row",
        {
            "route_classification": {
                "intent_class": "support_request",
                "support_scope": "agora",
                "agora_route": "automation",
                "automation_subcategory": "",
            },
            "subcategory": "enablement",
        },
        frozenset({"backend_operation:enablement", "backend_operation"}),
    ),
)

"""Regression test ticket templates for the /automation/test console.

Each template mirrors the classification gold samples used by the account
route pipeline prompts and tests, so a sent ticket reliably lands in the
intended automation subcategory (fraud_account / enablement /
account_suspension).
"""

from __future__ import annotations

from typing import Any

FRAUD_ACCOUNT_CATEGORY = "fraud_account"
ENABLEMENT_CATEGORY = "enablement"
ACCOUNT_SUSPENSION_CATEGORY = "account_suspension"

AUTOMATION_TEST_CATEGORY_IDS = (
    FRAUD_ACCOUNT_CATEGORY,
    ENABLEMENT_CATEGORY,
    ACCOUNT_SUSPENSION_CATEGORY,
)

# 32-char hex App ID so the deterministic enablement path can extract it.
ENABLEMENT_TEMPLATE_APP_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

AUTOMATION_TEST_TEMPLATES: dict[str, dict[str, Any]] = {
    FRAUD_ACCOUNT_CATEGORY: {
        "id": FRAUD_ACCOUNT_CATEGORY,
        "label": "Fraud Account",
        "description": "Account blocked for suspicious activity with complete review information.",
        "subject": "Account blocked for suspicious activity - please review",
        "body": (
            "Hello Agora team,\n\n"
            "Our account was blocked for suspicious activity and we received a request to "
            "provide review information. Please find the details below.\n\n"
            "Company Information:\n"
            "- Company: Zac Test Labs Inc.\n"
            "- Registration country: United States\n"
            "- Registered address: 100 Test Avenue, San Jose, CA\n\n"
            "Contact Information:\n"
            "- Name: Zac Tester\n"
            "- Email: zac.tester@example.com\n"
            "- Phone: +1 555 010 8888\n\n"
            "Use Case:\n"
            "We build a live-streaming classroom product and use Agora real-time video and "
            "audio to connect teachers with students in small groups.\n\n"
            "Payment Information:\n"
            "Usage is covered by corporate credit-card top-ups managed by our finance team. "
            "No card details are included in this ticket."
        ),
        "expected": [
            "Routes to Account & Billing -> fraud_account (automated)",
            "Internal handoff email [Fraud Account Review] is sent to the fraud reviewer inbox",
            "Public customer reply contains the 24-hour commitment sentence",
            "Zendesk ticket is assigned to the fraud reviewer and a Slack 'Fraud Account' notification fires",
            "Ticket is NOT auto-solved; it stays pending for manual review",
            "Variant: remove any of the four info groups before sending -> exactly one follow-up question, no 24h sentence",
        ],
    },
    ENABLEMENT_CATEGORY: {
        "id": ENABLEMENT_CATEGORY,
        "label": "Enablement",
        "description": "Explicit request to enable a registered backend feature (Media Relay) with App ID.",
        "subject": "Please enable Media Relay for our project",
        "body": (
            "Hello Agora team,\n\n"
            "Please enable Media Relay from your end for our project.\n\n"
            f"App ID: {ENABLEMENT_TEMPLATE_APP_ID}\n\n"
            "We are building a live event platform and need Media Relay to bridge presenters "
            "between two channels. Thank you."
        ),
        "expected": [
            "Routes to Backend Operation -> enablement (deterministic fast path for Media Relay)",
            "Internal handoff email [Enablement Request] is sent to the enablement inbox",
            "Customer reply confirms submission with an activation SLA of up to 24 hours",
            "After an internal reply confirms completion: completion reply is published, ticket solved, local case closed",
            "No Slack notification for this category",
        ],
    },
    ACCOUNT_SUSPENSION_CATEGORY: {
        "id": ACCOUNT_SUSPENSION_CATEGORY,
        "label": "Account Suspension",
        "description": "Non-fraud suspension report with a single intent (no refund or other requests).",
        "subject": "Account suspended after balance ran out",
        "body": (
            "Hello,\n\n"
            "Our Agora account is suspended and the console says the account has been stopped "
            "after our balance ran out. We topped up yesterday but the account is still not "
            "accessible.\n\n"
            "Please help restore the account."
        ),
        "expected": [
            "Routes to Account & Billing -> account_suspension (automated)",
            "First customer reply asks which email the relevant team should use (awaiting_customer)",
            "After you reply from the test mailbox with a single email address: internal handoff email, closing reply with 24h sentence, ticket solved, local case closed",
            "Slack 'Account Suspension' notification fires on the closing reply",
            "Variant: reply with multiple or ambiguous addresses -> human_review_required",
        ],
    },
}


def automation_test_template_list() -> list[dict[str, Any]]:
    """Return the templates as an ordered list for the console API."""
    return [dict(AUTOMATION_TEST_TEMPLATES[category]) for category in AUTOMATION_TEST_CATEGORY_IDS]


def automation_test_template(category: str) -> dict[str, Any] | None:
    normalized = str(category or "").strip()
    template = AUTOMATION_TEST_TEMPLATES.get(normalized)
    return dict(template) if template is not None else None

"""Deterministic fraud_account reply basis for the Hermes persona phase.

Production fraud replies (tickets 13710/13616/13426/13359) derive their
structure from CODE, not the model: the missing-field list uses fixed
display labels in a fixed order, the closing anchors a fixed commitment
sentence, and the complete-fields confirmation states the handoff and the
24-hour contact window. Offline evaluation (2026-10-09, gpt-6-sol@medium,
three prompt iterations) showed prompt-only enforcement of those anchors
is unreliable — the server now builds the structured parts and the persona
only composes the narrative around them (plan "Production fraud_account
客户回复风格对齐 v1", option B).

The label map and ordering mirror the legacy persona pipeline
(automation_persona._FIELD_LABELS) so both reply channels stay
byte-compatible on the structured parts.
"""
from __future__ import annotations

from typing import Any

# Canonical fraud_account display labels (source: legacy persona
# automation_persona._FIELD_LABELS — keep both maps aligned).
FRAUD_REPLY_FIELD_LABELS: dict[str, str] = {
    "account_type": "Account type",
    "name": "Name",
    "office_address": "Office address",
    "contact_number": "Official contact number",
    "contact_email": "Official contact email",
    "use_case_description": "Use-case description",
    "console_configuration": "Last known console configuration",
}

FRAUD_REPLY_FIELD_ORDER = (
    "account_type",
    "name",
    "office_address",
    "contact_number",
    "contact_email",
    "use_case_description",
    "console_configuration",
)

ASK_LEAD_IN_ANCHOR = "I can help coordinate a review of your account."
ASK_CLOSING_ANCHOR = (
    "Once you provide this information, I will continue coordinating the review."
)
CONFIRMATION_ANCHOR = (
    "I have forwarded the fraud review request and the information you "
    "provided to the relevant team."
)
CONTACT_COMMITMENT_ANCHOR = "The relevant team will contact you within 24 hours."


def fraud_field_display_label(field: str) -> str:
    normalized = str(field or "").strip()
    if normalized in FRAUD_REPLY_FIELD_LABELS:
        return FRAUD_REPLY_FIELD_LABELS[normalized]
    return normalized.replace("_", " ").capitalize() if normalized else normalized


def build_fraud_reply_basis(
    *,
    missing_fields: list[str] | None,
    collected_fields: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the server-authoritative structured parts of a fraud reply.

    The persona renders these verbatim and only adds narrative (lead-in,
    collected-fact restatement sentences, transitions). Unknown fields keep
    their given order after the canonical seven so nothing is dropped.
    """
    known = {f for f in FRAUD_REPLY_FIELD_ORDER if f in set(missing_fields or [])}
    extras = [
        f for f in (missing_fields or []) if f not in FRAUD_REPLY_FIELD_ORDER
    ]
    ordered_missing = [f for f in FRAUD_REPLY_FIELD_ORDER if f in known] + extras

    collected = {
        str(key): str(value)
        for key, value in dict(collected_fields or {}).items()
        if str(key) in FRAUD_REPLY_FIELD_LABELS and str(value or "").strip()
    }
    collected_pairs = [
        {
            "label": FRAUD_REPLY_FIELD_LABELS[field],
            "value": collected[field],
        }
        for field in FRAUD_REPLY_FIELD_ORDER
        if field in collected
    ]

    basis: dict[str, Any] = {
        "kind": "fraud_account_reply_basis_v1",
        "draft_language": "English",
        "collected_facts": collected_pairs,
    }
    if ordered_missing:
        labels = [fraud_field_display_label(f) for f in ordered_missing]
        basis["missing_fields"] = ordered_missing
        basis["field_labels"] = dict(zip(ordered_missing, labels))
        if len(labels) <= 2:
            basis["ask_layout"] = "prose"
            core = "please share your {} and {}".format(
                labels[0].lower(), labels[1].lower()
            ) if len(labels) == 2 else "please share your {}".format(labels[0].lower())
            basis["ask_sentence"] = f"To proceed, {core}."
        else:
            basis["ask_layout"] = "bullets"
            basis["ask_connector"] = "To proceed, please provide:"
            basis["ask_bullets"] = "\n".join(f"- {label}" for label in labels)
        basis["lead_in_anchor"] = ASK_LEAD_IN_ANCHOR
        basis["closing_anchor"] = ASK_CLOSING_ANCHOR
    else:
        basis["missing_fields"] = []
        basis["confirmation_anchor"] = CONFIRMATION_ANCHOR
        basis["contact_commitment_anchor"] = CONTACT_COMMITMENT_ANCHOR
    return basis

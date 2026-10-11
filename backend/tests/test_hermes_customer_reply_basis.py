from __future__ import annotations

import pytest

from backend.services.hermes_customer_reply_basis import (
    HERMES_CUSTOMER_REPLY_BASIS_KIND,
    HermesReplyBasisError,
    build_hermes_customer_reply_basis,
    validate_customer_reply_draft,
    project_greeting,
)


def _basis(**overrides):
    payload = dict(
        source_turn_id="turn-1",
        direction="automation",
        route="fraud_account",
        stage="request_missing_information",
        collected_facts={"name": "Jordan Lee"},
        missing_fields=["office_address"],
        trusted_conclusion={"status": "missing_fields"},
        allowed_claims=["state:missing_fields"],
        forbidden_topics=["payment_information"],
    )
    payload.update(overrides)
    return build_hermes_customer_reply_basis(**payload)


def test_basis_is_versioned_and_turn_bound():
    basis = _basis()
    assert basis["kind"] == HERMES_CUSTOMER_REPLY_BASIS_KIND
    assert basis["reply_owner"] == "hermes_persona"
    assert basis["source_turn_id"] == "turn-1"
    assert basis["publication_policy"] == "auto"


def test_validator_accepts_natural_wording_and_single_greeting():
    basis = _basis()
    result = validate_customer_reply_draft(
        "Hi Customer,\n\nThanks for the details. Please share the office address so we can continue.",
        basis,
    )
    assert result["decision"] == "pass"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("Hi Customer,\n\nPlease send your credit card number.", "payment_information_request"),
        ("Hi Customer,\n\nThe workflow will update the ticket.", "internal_implementation_exposed"),
        ("Hi Customer,\n\nHi Customer,\n\nThanks.", "duplicate_greeting"),
    ],
)
def test_validator_rejects_unsafe_or_duplicate_content(content, code):
    with pytest.raises(HermesReplyBasisError) as exc:
        validate_customer_reply_draft(content, _basis())
    assert exc.value.code == code


def test_investigation_basis_is_manual():
    basis = _basis(direction="investigation", route="investigation", publication_policy=None)
    assert basis["publication_policy"] == "manual"


def test_greeting_projection_keeps_one_fallback_greeting():
    rendered = project_greeting("Hi Jordan,\n\nThanks for the update.", "Customer")
    assert rendered.count("Hi ") == 1
    assert rendered.startswith("Hi Customer,")


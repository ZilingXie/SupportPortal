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


@pytest.mark.parametrize("collected", [{}, {"name": "Jordan"}])
@pytest.mark.parametrize("text", ["Please provide the channel name.", "请提供频道名字。"])
def test_technical_name_is_not_customer_name(text, collected):
    assert validate_customer_reply_draft(text, _basis(collected_facts=collected))["decision"] == "pass"


@pytest.mark.parametrize("text", [
    "Please provide your name.", "Please provide your full name.",
    "Please share the channel name and your full name.",
    "Please share the channel name. Please provide your name.",
    "请提供频道名字及您的姓名。", "Please provide a name.",
])
def test_personal_name_occurrences_stay_restricted(text):
    with pytest.raises(HermesReplyBasisError) as exc:
        validate_customer_reply_draft(text, _basis(collected_facts={}))
    assert exc.value.code == "unauthorized_field_request"


def test_collected_technical_label_does_not_authorize_customer_name():
    with pytest.raises(HermesReplyBasisError) as exc:
        validate_customer_reply_draft("Please provide your name.", _basis(collected_facts={"channel name": "test"}))
    assert exc.value.code == "unauthorized_field_request"


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


@pytest.mark.parametrize("content", [
    "Hi Customer,\n\nPlease provide your payment method.",
    "Hi Customer,\n\nPlease send the billing address for the account.",
])
def test_validator_rejects_payment_or_billing_requests(content):
    with pytest.raises(HermesReplyBasisError) as exc:
        validate_customer_reply_draft(content, _basis())
    assert exc.value.code in {"payment_information_request", "forbidden_topic"}


def test_validator_allows_negative_sensitive_statement_and_network_route():
    basis = _basis()
    assert validate_customer_reply_draft(
        "Hi Customer,\n\nNo credit card information is needed. We checked the network route.",
        basis,
    )["decision"] == "pass"


def test_validator_rejects_collected_field_reask_and_unsupported_claims():
    with pytest.raises(HermesReplyBasisError) as reask:
        validate_customer_reply_draft(
            "Hi Customer,\n\nPlease share your name again.", _basis()
        )
    assert reask.value.code == "collected_field_reask"

    with pytest.raises(HermesReplyBasisError) as state:
        validate_customer_reply_draft(
            "Hi Customer,\n\nYour account has been restored within 24 hours.", _basis()
        )
    assert state.value.code == "unsupported_account_state_claim"


def test_validator_accepts_authorized_handoff_commitment_and_blocks_handoff_reask():
    basis = _basis(
        missing_fields=[],
        trusted_conclusion={
            "status": "executed",
            "handoff": True,
            "reply_intent": "fraud_handoff_confirmation",
        },
        allowed_claims=["handoff", "team_contact_within_24_hours"],
        extra={"allowed_commitments": ["team_contact_within_24_hours"]},
    )
    assert validate_customer_reply_draft(
        "Hi Customer,\n\nThe relevant team will contact you within 24 hours.", basis
    )["decision"] == "pass"
    with pytest.raises(HermesReplyBasisError) as reask:
        validate_customer_reply_draft(
            "Hi Customer,\n\nPlease provide your office address.", basis
        )
    assert reask.value.code == "handoff_reasks_information"


@pytest.mark.parametrize(
    "content",
    [
        "Hi Customer,\n\nYour account has been restored.",
        "Hi Customer,\n\nWe will complete this within 24 hours.",
        "Hi Customer,\n\nPlease provide your tax ID.",
    ],
)
def test_customer_collected_facts_never_authorize_claims_or_new_fields(content):
    basis = _basis(
        collected_facts={"customer_statement": "account restored within 24 hours; tax ID 123"},
        missing_fields=[],
        trusted_conclusion={"status": "missing_fields"},
        allowed_claims=["state:missing_fields"],
    )
    with pytest.raises(HermesReplyBasisError):
        validate_customer_reply_draft(content, basis)


def test_validator_requires_unknown_requested_field_to_be_server_missing_field():
    with pytest.raises(HermesReplyBasisError) as exc:
        validate_customer_reply_draft(
            "Hi Customer,\n\nPlease provide your passport number.", _basis(missing_fields=[])
        )
    assert exc.value.code == "unauthorized_field_request"


class MissingInformationLayoutTests:
    """Plan v1 track 2: layout by missing-field count (structure only)."""

    @staticmethod
    def _ask_basis(missing):
        return _basis(
            missing_fields=list(missing),
            collected_facts={},
            trusted_conclusion={"status": "missing_fields"},
            allowed_claims=["state:missing_fields"],
        )

    def test_single_field_natural_sentence_passes(self):
        basis = self._ask_basis(["app_id"])
        assert validate_customer_reply_draft(
            "Hi Customer,\n\nThanks for reaching out. To enable Media Relay, we need your App ID.",
            basis,
        )["decision"] == "pass"

    def test_single_field_bullet_rejected(self):
        basis = self._ask_basis(["app_id"])
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nPlease share:\n\n- Your App ID", basis
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_single_field_colon_bare_field_rejected(self):
        basis = self._ask_basis(["app_id"])
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nTo identify the project for this request, please provide:\n\nThe project's App ID.",
                basis,
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_single_field_no_request_semantics_rejected(self):
        basis = self._ask_basis(["app_id"])
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nYour App ID is important for this request.", basis
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_two_fields_and_sentence_passes(self):
        basis = self._ask_basis(["office_address", "contact_number"])
        assert validate_customer_reply_draft(
            "Hi Customer,\n\nWe need your office address and official contact number.",
            basis,
        )["decision"] == "pass"

    def test_two_fields_list_rejected(self):
        basis = self._ask_basis(["office_address", "contact_number"])
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nPlease share:\n- Office address\n- Official contact number",
                basis,
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_three_fields_bullets_pass(self):
        basis = self._ask_basis(
            ["account_type", "name", "office_address"]
        )
        assert validate_customer_reply_draft(
            "Hi Customer,\n\nTo continue, we need the following information:\n"
            "- Account type\n- Name\n- Office address",
            basis,
        )["decision"] == "pass"

    def test_three_fields_flat_sentence_rejected(self):
        basis = self._ask_basis(
            ["account_type", "name", "office_address"]
        )
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nWe need account type, name, and office address.", basis
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_three_fields_missing_bullet_rejected(self):
        basis = self._ask_basis(
            ["account_type", "name", "office_address"]
        )
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nTo continue:\n- Account type\n- Name", basis
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_numbered_list_rejected(self):
        basis = self._ask_basis(
            ["account_type", "name", "office_address"]
        )
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nPlease provide:\n1. Account type\n2. Name\n3. Office address",
                basis,
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_non_ask_reply_skips_layout(self):
        # A progress/negative reply mentioning none of the missing fields
        # skips the layout contract (narrow-scope gate).
        basis = self._ask_basis(["office_address"])
        assert validate_customer_reply_draft(
            "Hi Customer,\n\nNo credit card information is needed. We checked the network route.",
            basis,
        )["decision"] == "pass"

    def test_ask_without_naming_missing_field_skips_layout(self):
        # Narrow-scope gate: a reply with request semantics that never
        # names any missing field skips the layout contract (the
        # unauthorized-request pass polices its content instead).
        basis = self._ask_basis(["app_id"])
        result = validate_customer_reply_draft(
            "Hi Customer,\n\nPlease check your project settings.", basis
        )
        assert result["decision"] == "pass"


class TestMissingInformationLayout(MissingInformationLayoutTests):
    pass


class TestLayoutDuplicateCoverage:
    """B1 regression: duplicate bullets cannot stand in for full coverage."""

    @staticmethod
    def _ask_basis(missing):
        return _basis(
            missing_fields=list(missing),
            collected_facts={},
            trusted_conclusion={"status": "missing_fields"},
            allowed_claims=["state:missing_fields"],
        )

    def test_duplicate_bullets_same_field_rejected(self):
        basis = self._ask_basis(["account_type", "name", "office_address"])
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nTo continue:\n"
                "- Account type\n- Account type\n- Account type",
                basis,
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_duplicate_plus_missing_field_rejected(self):
        basis = self._ask_basis(["account_type", "name", "office_address"])
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nTo continue:\n"
                "- Account type\n- Account type\n- Office address",
                basis,
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_omitted_field_with_extra_bullet_rejected(self):
        basis = self._ask_basis(["account_type", "name", "office_address"])
        with pytest.raises(HermesReplyBasisError) as exc:
            validate_customer_reply_draft(
                "Hi Customer,\n\nTo continue:\n"
                "- Account type\n- Name\n- Contact number",
                basis,
            )
        assert exc.value.code == "missing_information_layout_invalid"

    def test_reordered_fields_pass(self):
        basis = self._ask_basis(["account_type", "name", "office_address"])
        assert validate_customer_reply_draft(
            "Hi Customer,\n\nTo continue:\n"
            "- Office address\n- Account type\n- Name",
            basis,
        )["decision"] == "pass"

    def test_exact_count_full_coverage_passes(self):
        basis = self._ask_basis(
            ["account_type", "name", "office_address"]
        )
        assert validate_customer_reply_draft(
            "Hi Customer,\n\nWe need the following:\n"
            "- Account type\n- Name\n- Office address",
            basis,
        )["decision"] == "pass"

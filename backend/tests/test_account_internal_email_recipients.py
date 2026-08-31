from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend.services.account_internal_email_recipients import (
    ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV,
    ECS_ACCOUNT_ONLY_ENV,
    ENABLEMENT_RECIPIENTS_JSON_ENV,
    FRAUD_RECIPIENTS_JSON_ENV,
    AccountInternalEmailRecipientError,
    resolve_account_internal_email_recipients,
    validate_ecs_account_internal_email_recipients,
)
from backend.services.account_verification_automation import (
    build_account_verification_internal_email_payload,
)
from backend.services.billing_automation import (
    BILLING_ACTION_ACCOUNT_SUSPENSION,
    build_billing_internal_email_payload,
)
from backend.services.enablement_automation import build_enablement_automation_result_from_fields
from backend.services.internal_email_payload import resolve_account_internal_email_recipient


RECIPIENT_ENVS = (
    ENABLEMENT_RECIPIENTS_JSON_ENV,
    FRAUD_RECIPIENTS_JSON_ENV,
    ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV,
)


class AccountInternalEmailRecipientTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(
            os.environ,
            {
                ECS_ACCOUNT_ONLY_ENV: "",
                **{name: "" for name in RECIPIENT_ENVS},
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    @staticmethod
    def _json(to: list[str], cc: list[str]) -> str:
        return json.dumps({"to": to, "cc": cc})

    def test_json_contract_normalizes_and_deduplicates_addresses(self) -> None:
        with patch.dict(
            os.environ,
            {
                ENABLEMENT_RECIPIENTS_JSON_ENV: self._json(
                    ["team@example.com", "TEAM@example.com"],
                    ["owner@example.com", "OWNER@example.com"],
                )
            },
            clear=False,
        ):
            recipients = resolve_account_internal_email_recipients("enablement")

        self.assertEqual(recipients.to, ("team@example.com",))
        self.assertEqual(recipients.cc, ("owner@example.com",))
        self.assertEqual(recipients.source, "environment_json")

    def test_json_contract_rejects_missing_extra_empty_and_invalid_values_without_leaking_address(self) -> None:
        invalid_values = (
            "not-json",
            json.dumps({"to": ["team@example.com"]}),
            json.dumps({"to": ["team@example.com"], "cc": [], "extra": []}),
            json.dumps({"to": ["secret-address"], "cc": ["owner@example.com"]}),
        )
        for value in invalid_values:
            with self.subTest(value=value), patch.dict(
                os.environ,
                {ENABLEMENT_RECIPIENTS_JSON_ENV: value},
                clear=False,
            ), self.assertRaises(AccountInternalEmailRecipientError) as context:
                resolve_account_internal_email_recipients("enablement")
            self.assertEqual(context.exception.code, "account_internal_email_recipient_invalid")
            self.assertNotIn("secret-address", str(context.exception))

    def test_ecs_requires_all_three_json_configs(self) -> None:
        valid = self._json(["team@example.com"], ["owner@example.com"])
        with patch.dict(
            os.environ,
            {
                ECS_ACCOUNT_ONLY_ENV: "1",
                ENABLEMENT_RECIPIENTS_JSON_ENV: valid,
                FRAUD_RECIPIENTS_JSON_ENV: valid,
                ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV: "",
            },
            clear=False,
        ), self.assertRaises(AccountInternalEmailRecipientError) as context:
            validate_ecs_account_internal_email_recipients()

        self.assertEqual(context.exception.code, "account_internal_email_recipient_missing")
        self.assertEqual(context.exception.config_key, ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV)

    def test_ec2_legacy_single_address_and_owner_cc_remain_supported(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": "legacy@example.com",
                "AUTOMATION_INTERNAL_EMAIL_CC": "owner@example.com",
            },
            clear=False,
        ):
            recipients = resolve_account_internal_email_recipients("enablement")

        self.assertEqual(recipients.to, ("legacy@example.com",))
        self.assertEqual(recipients.cc, ("owner@example.com",))
        self.assertEqual(recipients.source, "legacy_environment")

    def test_persisted_recipients_win_over_later_environment_changes(self) -> None:
        payload = {
            "to": "first@example.com",
            "to_addresses": ["first@example.com"],
            "cc_addresses": ["owner@example.com"],
            "recipient_config_key": ENABLEMENT_RECIPIENTS_JSON_ENV,
        }
        with patch.dict(
            os.environ,
            {
                ECS_ACCOUNT_ONLY_ENV: "1",
                ENABLEMENT_RECIPIENTS_JSON_ENV: self._json(
                    ["changed@example.com"], ["changed-owner@example.com"]
                ),
            },
            clear=False,
        ):
            resolved = resolve_account_internal_email_recipient(payload, handler="enablement")

        self.assertEqual(resolved["to_addresses"], ["first@example.com"])
        self.assertEqual(resolved["cc_addresses"], ["owner@example.com"])
        self.assertEqual(resolved["recipient_resolution_source"], "persisted_payload")

    def test_ecs_builders_persist_chain_specific_recipients(self) -> None:
        configs = {
            ECS_ACCOUNT_ONLY_ENV: "1",
            ENABLEMENT_RECIPIENTS_JSON_ENV: self._json(
                ["enablement@example.com"], ["owner@example.com"]
            ),
            FRAUD_RECIPIENTS_JSON_ENV: self._json(
                ["fraud@example.com"], ["owner@example.com"]
            ),
            ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV: self._json(
                ["suspension@example.com"], ["owner@example.com"]
            ),
        }
        with patch.dict(os.environ, configs, clear=False):
            enablement = build_enablement_automation_result_from_fields(
                collected_fields={
                    "app_id": "a" * 32,
                    "requested_feature": "media_relay",
                    "requested_feature_label": "Media Relay",
                },
                missing_fields=[],
                missing_customer_reply="",
                customer_message="Please enable Media Relay.",
                ticket_id="TK-1",
                account_case_id="AC-1",
            ).internal_email
            fraud = build_account_verification_internal_email_payload(
                ticket_id="TK-2",
                account_case_id="AC-2",
                customer_email="customer@example.com",
                collected_fields={"name": "Taylor"},
                missing_fields=[],
            )
            suspension = build_billing_internal_email_payload(
                action=BILLING_ACTION_ACCOUNT_SUSPENSION,
                collected_fields={"contact_email": "customer@example.com"},
                ticket_id="TK-3",
                customer_email="customer@example.com",
                customer_message="Please review the suspension.",
                billing_ticket_id="BT-3",
            )

        assert enablement is not None
        for payload, expected_to, expected_key in (
            (enablement, "enablement@example.com", ENABLEMENT_RECIPIENTS_JSON_ENV),
            (fraud, "fraud@example.com", FRAUD_RECIPIENTS_JSON_ENV),
            (suspension, "suspension@example.com", ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV),
        ):
            with self.subTest(expected_key=expected_key):
                self.assertEqual(payload["to_addresses"], [expected_to])
                self.assertEqual(payload["cc_addresses"], ["owner@example.com"])
                self.assertEqual(payload["recipient_config_key"], expected_key)


if __name__ == "__main__":
    unittest.main()

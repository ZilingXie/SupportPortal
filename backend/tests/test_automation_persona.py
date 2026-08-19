from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.automation_persona import (
    AutomationPersonaError,
    assert_no_trailing_automation_signature,
    build_account_automation_reply_facts,
    build_automation_reply_facts,
    customer_first_name,
    extract_automation_resolution_facts,
    render_automation_reply,
    validate_account_reply_contract,
)
from backend.services.detailed_invoice_field_extractor import extract_detailed_invoice_fields
from backend.services.billing_automation import build_billing_automation_result


class AutomationPersonaTests(unittest.TestCase):
    def test_enablement_submission_facts_use_canonical_name_without_identifiers(self) -> None:
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="submission_confirmation",
            known_information={
                "app_id": "abcdefabcdefabcdefabcdefabcdefab",
                "requested_feature": "media_relay",
                "requested_feature_label": "channel media rele",
            },
            source_facts=["App ID abcdefabcdefabcdefabcdefabcdefab requested channel media rele."],
        )

        self.assertEqual(facts["known_information"], {"requested_feature_name": "Media Relay"})
        self.assertEqual(facts["source_facts"], [])

    def test_enablement_resolution_facts_keep_safe_status_values(self) -> None:
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="resolution_update",
            known_information={
                "app_id": "abcdefabcdefabcdefabcdefabcdefab",
                "requested_feature": "media_relay",
                "requested_feature_label": "channel media rele",
                "resolution_status": "completed",
                "customer_action": "Try the feature again.",
            },
        )
        self.assertEqual(
            facts["known_information"],
            {
                "requested_feature_name": "Media Relay",
                "resolution_status": "completed",
                "customer_action": "Try the feature again.",
            },
        )

    def test_cross_channel_media_relay_uses_canonical_display_name(self) -> None:
        facts = build_automation_reply_facts(
            behavior="enablement", reply_intent="resolution_update",
            known_information={
                "app_id": "abcdefabcdefabcdefabcdefabcdefab",
                "requested_feature": "cross_channel_media_relay",
                "requested_feature_label": "channel media rele",
            },
        )
        self.assertEqual(facts["known_information"], {"requested_feature_name": "Media Relay"})

    def test_extractor_redacts_identifiers_email_and_raw_feature_label(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=json.dumps({"status": "completed", "customer_shareable_facts": ["Enabled."],
                             "customer_action": None, "next_step": None}),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            extract_automation_resolution_facts(
                behavior="enablement",
                source_text=("Ticket 12555 Account Case ID: AC-12555 App ID abcdefabcdefabcdefabcdefabcdefab "
                             "for customer@example.com channel media rele is enabled."),
                known_information={
                    "app_id": "abcdefabcdefabcdefabcdefabcdefab", "ticket_id": "12555",
                    "account_case_id": "AC-12555", "customer_email": "customer@example.com",
                    "requested_feature_label": "channel media rele",
                },
            )
        user_prompt = invoke.call_args.kwargs["user_prompt"]
        for forbidden in ("12555", "AC-12555", "abcdefabcdefabcdefabcdefabcdefab",
                          "customer@example.com", "channel media rele"):
            self.assertNotIn(forbidden, user_prompt)

    def test_final_reply_rejects_forbidden_app_id(self) -> None:
        facts = build_automation_reply_facts(
            behavior="enablement", reply_intent="resolution_update",
            known_information={"app_id": "abcdefabcdefabcdefabcdefabcdefab", "requested_feature": "media_relay"},
        )
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Media Relay is enabled for abcdefabcdefabcdefabcdefabcdefab.", model_name="persona-model"
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            with self.assertRaisesRegex(AutomationPersonaError, "automation_persona_forbidden_value"):
                render_automation_reply(
                    reply_facts=facts,
                    persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
                )

    def test_final_reply_allows_canonical_feature_label(self) -> None:
        facts = build_automation_reply_facts(
            behavior="enablement", reply_intent="resolution_update",
            known_information={"requested_feature": "media_relay", "requested_feature_label": "Media Relay"},
        )
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(text="Media Relay is now enabled.", model_name="persona-model")
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
            )
        self.assertIn("Media Relay", result.content)

    def test_enablement_submission_prompt_uses_canonical_name(self) -> None:
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="submission_confirmation",
            known_information={
                "app_id": "abcdefabcdefabcdefabcdefabcdefab",
                "requested_feature": "media_relay",
                "requested_feature_label": "channel media rele",
            },
        )
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thank you for reaching out. We are reviewing it with our internal team and will keep you posted "
                "as soon as we have an update. Activation may take up to 24 hours, and the change window is "
                "Monday-Friday.\n\nWe appreciate your patience."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
                account_scope=True,
            )

        user_prompt = invoke.call_args.kwargs["user_prompt"]
        system_prompt = invoke.call_args.kwargs["system_prompt"]
        self.assertIn("Media Relay", user_prompt)
        self.assertNotIn("abcdefabcdefabcdefabcdefabcdefab", user_prompt)
        self.assertNotIn("channel media rele", user_prompt)
        self.assertIn("Do not repeat identifier values", system_prompt)
        self.assertIn("canonical product or feature display name", system_prompt)
        self.assertIn("Thank the customer", system_prompt)
        self.assertIn("Semantic fields such as ownership_state", system_prompt)
        self.assertNotIn("The assigned Support Engineer has started", user_prompt)
        self.assertIn("Thank you for reaching out", result.content)
        self.assertNotIn("channel media rele", result.content)

    def test_account_submission_facts_are_semantic_not_customer_copy(self) -> None:
        facts = build_account_automation_reply_facts(
            handler="enablement",
            action="enablement",
            missing_fields=[],
            collected_fields={"requested_feature": "media_relay"},
            submitted=True,
        )

        self.assertEqual(facts["resolution_status"], "internal_review_in_progress")
        self.assertEqual(facts["ownership_state"], "support_owned_internal_review")
        self.assertEqual(facts["customer_update_commitment"], "update_when_available")
        self.assertEqual(facts["performed_actions"], [])
        self.assertIsNone(facts["next_step"])

    def test_account_submission_rejects_third_person_support_owner_copy(self) -> None:
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="submission_confirmation",
        )
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "The assigned Support Engineer has started coordinating the request with the internal team, and the "
                "case is currently in progress with them. The assigned Support Engineer will continue monitoring the "
                "request and proactively update you when there is progress. Activation may take up to 24 hours, "
                "and the change window is Monday-Friday."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            with self.assertRaisesRegex(AutomationPersonaError, "ownership_contract_failed"):
                render_automation_reply(
                    reply_facts=facts,
                    persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
                    account_scope=True,
                )

    def test_old_submission_facts_are_normalized_before_v9_prompt(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "We are reviewing the request with our internal team and will keep you posted. Activation may take "
                "up to 24 hours, and the change window is Monday-Friday."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            render_automation_reply(
                reply_facts={
                    "behavior": "enablement",
                    "reply_intent": "submission_confirmation",
                    "performed_actions": [
                        "The assigned Support Engineer has started coordinating the request with the internal team."
                    ],
                    "next_step": (
                        "The assigned Support Engineer will continue monitoring the request and proactively update the "
                        "customer when there is progress."
                    ),
                    "resolution_status": "in_progress_with_internal_team",
                },
                persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
                account_scope=True,
            )

        user_prompt = invoke.call_args.kwargs["user_prompt"]
        self.assertNotIn("The assigned Support Engineer", user_prompt)
        self.assertIn("support_owned_internal_review", user_prompt)

    def test_render_uses_facts_and_pinned_persona(self) -> None:
        facts = build_automation_reply_facts(
            behavior="detailed_invoice",
            reply_intent="request_missing_information",
            missing_information=["Transaction ID"],
            resolution_status="awaiting_customer",
            customer_name="jack Gold",
        )
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Could you share the Transaction ID? Once I have it, I will continue coordinating the request.",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={
                    "persona_key": "default-support",
                    "content": {
                        "instruction": "Warm",
                        "signature": "Best,\nSid\nSupport Engineer 2",
                    },
                },
                account_scope=True,
            )

        self.assertEqual(result.content, f"Hi Jack,\n\n{response.text}")
        self.assertEqual(result.model, "persona-model")
        self.assertIn('"missing_information"', invoke.call_args.kwargs["user_prompt"])
        self.assertIn("Warm", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("Do not write a greeting, signoff", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("Hi Jack,", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("warm, natural sentences", invoke.call_args.kwargs["system_prompt"])
        self.assertNotIn("Best,\nSid\nSupport Engineer 2", invoke.call_args.kwargs["system_prompt"])

    def test_customer_first_name_uses_first_token_and_safe_fallback(self) -> None:
        self.assertEqual(customer_first_name("  Jack   Gold  "), "Jack")
        self.assertEqual(customer_first_name("md anisur rahman"), "Md")
        self.assertEqual(customer_first_name("mD anisur rahman"), "MD")
        self.assertEqual(customer_first_name("陈小明"), "陈小明")
        self.assertEqual(customer_first_name("Mary-Jane Watson"), "Mary-Jane")
        self.assertEqual(customer_first_name("customer@example.com"), "Customer")
        self.assertEqual(customer_first_name("Jack<script>"), "Customer")
        self.assertEqual(customer_first_name(""), "Customer")

    def test_render_removes_model_generated_greeting_before_adding_configured_greeting(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Hi Jack, I am coordinating the request with our internal team and will keep you updated. Activation "
                "may take up to 24 hours, and the change window is Monday-Friday."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            result = render_automation_reply(
                reply_facts={
                    "behavior": "enablement",
                    "reply_intent": "submission_confirmation",
                    "customer_first_name": "Jack",
                },
                persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
                account_scope=True,
            )

        self.assertEqual(
            result.content,
            "Hi Jack,\n\nI am coordinating the request with our internal team and will keep you updated. Activation may "
            "take up to 24 hours, and the change window is Monday-Friday.",
        )

    def test_legacy_signoff_name_is_ignored(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(text="The request is complete.", model_name="persona-model")
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            result = render_automation_reply(
                reply_facts={"behavior": "quota", "reply_intent": "resolution_update"},
                persona_assignment={"content": {"instruction": "Warm", "signoff_name": "Maya"}},
            )

        self.assertEqual(result.content, "Hi Customer,\n\nThe request is complete.")

    def test_trailing_signature_is_rejected_without_rewriting_body(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="This is the best next step for the request.\n\nBest,\nSid\nSupport Engineer 2",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            with self.assertRaisesRegex(AutomationPersonaError, "automation_persona_signature_forbidden"):
                render_automation_reply(
                    reply_facts={"behavior": "quota", "reply_intent": "resolution_update"},
                    persona_assignment={"content": {"instruction": "Warm"}},
                )

        body = "This is the best next step with regards to the current request.\n\nThanks for your patience while we review it."
        assert_no_trailing_automation_signature(body)
        self.assertEqual(
            body,
            "This is the best next step with regards to the current request.\n\nThanks for your patience while we review it.",
        )

        for signed_reply in (
            "Update complete.\n\nBest, Sid",
            "Update complete.\n\nRegards,",
            "Update complete.\n\n此致\nSid",
        ):
            with self.subTest(signed_reply=signed_reply):
                with self.assertRaisesRegex(AutomationPersonaError, "automation_persona_signature_forbidden"):
                    assert_no_trailing_automation_signature(signed_reply)

    def test_signature_guard_only_inspects_the_reply_tail(self) -> None:
        body = (
            "Thanks\n"
            "we reviewed the request details and confirmed the expected behavior.\n"
            "The next step is to validate the configuration.\n"
            "We will keep you posted when that validation is complete.\n"
            "No action is required from you now."
        )
        assert_no_trailing_automation_signature(body)

    def test_fraud_handoff_requires_relevant_team_and_24_hours(self) -> None:
        facts = {
            "behavior": "fraud_account",
            "reply_intent": "fraud_handoff_confirmation",
        }
        validate_account_reply_contract(
            "The relevant team will contact you within 24 hours.",
            facts,
        )
        with self.assertRaisesRegex(AutomationPersonaError, "fraud_handoff_contract_failed"):
            validate_account_reply_contract("We received your request and will review it.", facts)
        for invalid_reply in (
            "The relevant team will not contact you within 24 hours.",
            "We cannot guarantee the relevant team will contact you within 24 hours.",
            "Will the relevant team contact you within 24 hours?",
        ):
            with self.subTest(invalid_reply=invalid_reply):
                with self.assertRaisesRegex(AutomationPersonaError, "fraud_handoff_contract_failed"):
                    validate_account_reply_contract(invalid_reply, facts)

    def test_enablement_submission_requires_sla_and_change_window(self) -> None:
        facts = {
            "behavior": "enablement",
            "reply_intent": "submission_confirmation",
        }
        with self.assertRaisesRegex(AutomationPersonaError, "enablement_submission_contract_failed"):
            validate_account_reply_contract("We are reviewing the request.", facts)
        validate_account_reply_contract(
            "I am coordinating activation and will keep you updated. It may take up to 24 hours, and the change "
            "window is Monday-Friday.",
            facts,
        )
        with self.assertRaisesRegex(AutomationPersonaError, "enablement_submission_contract_failed"):
            validate_account_reply_contract(
                "Activation will not happen within 24 hours; changes occur Monday-Friday.",
                facts,
            )

    def test_suspension_contact_contract_requires_email_close_and_reopen_terms(self) -> None:
        facts = {
            "behavior": "account_suspension",
            "reply_intent": "account_suspension_contact_confirmation_request",
        }
        validate_account_reply_contract(
            "Which email is most convenient for you? Should we use the email on this ticket? "
            "The relevant team will contact you within 24 hours; the ticket will close after handoff, "
            "and you can reopen it if nobody contacts you.",
            facts,
        )
        with self.assertRaisesRegex(AutomationPersonaError, "suspension_contact_contract_failed"):
            validate_account_reply_contract("Please share an email address.", facts)
        for invalid_reply in (
            "Which email is best for you, including the email on this ticket? The relevant team will not contact "
            "you within 24 hours; this ticket will close, and you can reopen it.",
            "Which email is best for you, including the email on this ticket? Can the relevant team contact you "
            "within 24 hours? This ticket will close, and you can reopen it.",
        ):
            with self.subTest(invalid_reply=invalid_reply):
                with self.assertRaisesRegex(AutomationPersonaError, "suspension_contact_contract_failed"):
                    validate_account_reply_contract(invalid_reply, facts)

    def test_completion_contract_requires_positive_enablement_and_closure(self) -> None:
        facts = {
            "behavior": "enablement",
            "reply_intent": "enablement_completed_and_close",
        }
        validate_account_reply_contract(
            "The feature is enabled, and this ticket is closing.",
            facts,
            close_after_publish=True,
        )
        for invalid_reply in (
            "The feature is not enabled, but this ticket is closing.",
            "The feature is enabled, but this ticket will not close.",
        ):
            with self.subTest(invalid_reply=invalid_reply):
                with self.assertRaisesRegex(AutomationPersonaError, "completion_contract_failed"):
                    validate_account_reply_contract(
                        invalid_reply,
                        facts,
                        close_after_publish=True,
                    )

    def test_conflicting_intents_and_legacy_fraud_close_are_rejected(self) -> None:
        with self.assertRaisesRegex(AutomationPersonaError, "account_reply_intent_conflict"):
            validate_account_reply_contract(
                "The request is being reviewed.",
                {"behavior": "enablement", "reply_intent": "submission_confirmation"},
                top_level_reply_intent="resolution_update",
            )
        with self.assertRaisesRegex(AutomationPersonaError, "legacy_fraud_handoff_close_intent"):
            validate_account_reply_contract(
                "The relevant team will contact you within 24 hours.",
                {"behavior": "fraud_account", "reply_intent": "fraud_handoff_and_close"},
            )

    def test_submission_reply_rejects_internal_team_ownership(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "We submitted the request. The internal team will follow up after review. Activation may take up to "
                "24 hours, and the change window is Monday-Friday."
            ),
            model_name="persona-model",
        )
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="submission_confirmation",
            known_information={"requested_feature": "media_relay"},
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            with self.assertRaisesRegex(AutomationPersonaError, "ownership_contract_failed"):
                render_automation_reply(
                    reply_facts=facts,
                    persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
                    account_scope=True,
                )

    def test_legacy_scope_does_not_apply_account_ownership_validator(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="The internal team will follow up after review.",
            model_name="persona-model",
        )
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="submission_confirmation",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
            )
        self.assertIn("The internal team will follow up", result.content)
        self.assertNotIn("Support Engineer is the customer's point of contact", invoke.call_args.kwargs["system_prompt"])

    def test_submission_reply_accepts_chinese_first_person_follow_up(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="我正在与内部团队协调这个请求，如果有进展我会第一时间同步给你。",
            model_name="persona-model",
        )
        facts = build_automation_reply_facts(
            behavior="quota",
            reply_intent="submission_confirmation",
            customer_language="zh",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm", "signature": "此致\nSid"}},
                account_scope=True,
            )
        self.assertIn("我正在与内部团队协调", result.content)

    def test_persona_failure_is_explicit(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: False, model="persona-model")
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile):
            with self.assertRaises(AutomationPersonaError):
                render_automation_reply(
                    reply_facts={"behavior": "quota", "reply_intent": "resolution_update"},
                    persona_assignment={"content": {"instruction": "Warm"}},
                )

    def test_internal_resolution_facts_are_extracted_before_persona(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="extractor-model")
        response = SimpleNamespace(
            text=json.dumps(
                {
                    "status": "completed",
                    "customer_shareable_facts": ["The request was completed."],
                    "customer_action": None,
                    "next_step": None,
                }
            )
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            result = extract_automation_resolution_facts(
                behavior="quota",
                source_text="Internal resolution: completed.",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["customer_shareable_facts"], ["The request was completed."])


class DetailedInvoiceFieldExtractorTests(unittest.TestCase):
    def test_extracts_invoice_fields_from_structured_model_output(self) -> None:
        response = SimpleNamespace(
            text=json.dumps(
                {
                    "status": "complete",
                    "fields": {
                        "issue_date": {"value": "6 May 2026"},
                        "transaction_id": {"value": "TX-123"},
                        "amount": {"value": "USD 10.00"},
                    },
                }
            )
        )
        result = extract_detailed_invoice_fields(
            message="Issue date 6 May 2026, transaction TX-123, amount USD 10.00",
            invoke=lambda **_: response,
        )
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.collected_fields["transaction_id"], "TX-123")
        self.assertFalse(result.requires_human_review)

    def test_behavior_builder_returns_facts_without_customer_copy(self) -> None:
        result = build_billing_automation_result(
            action="detailed_invoice",
            message="Please send a detailed invoice. Issue date: 6 May 2026.",
            ticket_id="TK-123",
            customer_email="customer@example.com",
            generate_customer_reply=False,
        )
        self.assertEqual(result.customer_reply, "")
        self.assertEqual(result.missing_fields, ["transaction_id", "amount"])


if __name__ == "__main__":
    unittest.main()

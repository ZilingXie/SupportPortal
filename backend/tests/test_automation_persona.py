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
    sanitize_enablement_completion_note,
    validate_account_reply_contract,
)
from backend.services.account_suspension_automation import closing_reply_facts
from backend.services.detailed_invoice_field_extractor import extract_detailed_invoice_fields
from backend.services.billing_automation import build_billing_automation_result


class AutomationPersonaTests(unittest.TestCase):
    def test_normal_render_uses_one_single_attempt_generation(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Thank you for submitting this request. We are reviewing it internally and we’ll get back to you within 24 hours.",
            model_name="persona-model",
        )
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as generate:
            result = render_automation_reply(
                reply_facts=closing_reply_facts(
                    confirmed_email="customer@example.com",
                    customer_name="Maya",
                ),
                persona_assignment={"content": {"instruction": "Warm and precise."}},
                account_scope=True,
            )

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(generate.call_args.kwargs["stage"], "automation_persona")
        self.assertEqual(generate.call_args.kwargs["max_attempts"], 1)
        self.assertEqual(result.generation_attempts, 1)
        self.assertEqual(result.safety_status, "passed")
        self.assertEqual(result.safety_issue_codes, ())

    def test_soft_business_omission_does_not_trigger_rewrite(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Thank you for submitting this request. We are reviewing it internally.",
            model_name="persona-model",
        )
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as generate:
            result = render_automation_reply(
                reply_facts=closing_reply_facts(
                    confirmed_email="customer@example.com",
                    customer_name="Maya",
                ),
                persona_assignment={"content": {"instruction": "Warm and precise."}},
                account_scope=True,
            )

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(result.content, f"Hi Maya,\n\n{response.text}")
        self.assertEqual(result.generation_attempts, 1)
        self.assertEqual(result.safety_issue_codes, ())

    def test_hard_safety_failure_rewrites_once_with_enumerated_feedback(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        responses = [
            SimpleNamespace(text="Hi Maya, we are reviewing this request.", model_name="persona-model"),
            SimpleNamespace(text="We are reviewing this request.", model_name="persona-model"),
        ]
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.automation_persona.invoke_responses_text", side_effect=responses
        ) as generate:
            result = render_automation_reply(
                reply_facts={"behavior": "quota", "reply_intent": "resolution_update"},
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )

        self.assertEqual(generate.call_count, 2)
        self.assertTrue(all(call.kwargs["max_attempts"] == 1 for call in generate.call_args_list))
        self.assertEqual(result.content, f"Hi Customer,\n\n{responses[1].text}")
        self.assertEqual(result.generation_attempts, 2)
        self.assertEqual(result.safety_issue_codes, ("automation_persona_greeting_forbidden",))
        revision_payload = json.loads(generate.call_args_list[1].kwargs["user_prompt"])["revision"]
        self.assertEqual(revision_payload["issue_codes"], ["automation_persona_greeting_forbidden"])
        self.assertIn("Rewrite the complete body", revision_payload["instruction"])

    def test_generation_failure_fails_without_retry(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.automation_persona.invoke_responses_text",
            side_effect=ValueError("provider failed"),
        ), self.assertRaisesRegex(
            AutomationPersonaError, "automation_persona_generation_failed"
        ):
            render_automation_reply(
                reply_facts={"behavior": "quota", "reply_intent": "resolution_update"},
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )

    def _archer_facts(self, intent: str, outcome: str) -> dict:
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent=intent,
            known_information={
                "app_id": "abcdefabcdefabcdefabcdefabcdefab",
                "requested_feature": "media_relay",
                "requested_feature_label": "media rele",
                "archer_outcome": outcome,
            },
            missing_information=[] if outcome == "enabled" else ["app_id"],
            customer_name="Ada Customer",
        )
        if outcome == "enabled":
            facts["completion_acknowledgement"] = "patience"
        return facts

    def test_archer_facts_are_canonical_and_forbid_appid(self) -> None:
        facts = self._archer_facts("enablement_archer_enabled", "enabled")
        self.assertEqual(
            facts["known_information"],
            {
                "archer_outcome": "enabled",
                "requested_feature_name": "Media Relay",
            },
        )
        self.assertEqual(facts["customer_first_name"], "Ada")
        self.assertIn("abcdefabcdefabcdefabcdefabcdefab", facts["_forbidden_values"])

    def test_archer_success_contract_keeps_future_tense_floor(self) -> None:
        facts = self._archer_facts("enablement_archer_enabled", "enabled")
        valid = (
            "Thank you for your patience. Media Relay is already enabled on your project. This case will be "
            "archived now. If you have further questions, you can open a new ticket."
        )
        natural_style = (
            "Thanks for waiting on this - good news: Media Relay is already enabled on your project, so you're "
            "all set. I'm closing this case now, but if any questions come up later, feel free to open a new "
            "ticket and we'll take it from there."
        )
        for valid_reply in (valid, natural_style):
            with self.subTest(valid_reply=valid_reply):
                normalized, close = validate_account_reply_contract(
                    valid_reply, facts, close_after_publish=True
                )
                self.assertTrue(close)
                self.assertEqual(normalized["reply_intent"], "enablement_archer_enabled")
        # v22: positive enabled/closing/media-relay wording is prompt-guided
        # only; the misleading future-tense claim stays a blocking floor.
        with self.assertRaisesRegex(AutomationPersonaError, "completion_contract_failed_enabled_state"):
            validate_account_reply_contract(
                valid.replace("already enabled", "will be enabled"),
                facts,
                close_after_publish=True,
            )
        # Media Relay mention itself is no longer a blocking requirement.
        validate_account_reply_contract(
            valid.replace("Media Relay", "the feature"), facts, close_after_publish=True
        )

    def test_archer_success_contract_allows_configuration_detail_when_customer_asks(self) -> None:
        # region/load are no longer required, but a reply that mentions them (for
        # example when the customer asked about capacity) must not be rejected
        facts = self._archer_facts("enablement_archer_enabled", "enabled")
        with_detail = (
            "Thank you for your patience. Media Relay is already enabled on your project in the oversea region "
            "with a maximum subscribe load of 50. This case will be archived now. If you have further "
            "questions, you can open a new ticket."
        )
        _, close = validate_account_reply_contract(with_detail, facts, close_after_publish=True)
        self.assertTrue(close)

    def test_archer_recoverable_contracts_request_a_replacement_without_overclaim(self) -> None:
        invalid_facts = self._archer_facts("enablement_appid_invalid", "appid_invalid")
        not_found_facts = self._archer_facts("enablement_appid_not_found", "project_not_found")
        validate_account_reply_contract(
            "The App ID format is invalid. Please provide the correct 32-character App ID.",
            invalid_facts,
        )
        validate_account_reply_contract(
            "I could not find a matching project for this App ID. Please verify and resend the App ID.",
            not_found_facts,
        )
        for facts, text in (
            (invalid_facts, "The App ID is invalid. Please send a 32-character App ID; Media Relay is enabled."),
            (not_found_facts, "No matching project was found. Please verify the App ID. This case is closing."),
        ):
            with self.subTest(text=text):
                with self.assertRaisesRegex(AutomationPersonaError, "archer_error_overclaim"):
                    validate_account_reply_contract(text, facts)

    def test_archer_persona_prompt_keeps_v22_and_excludes_appid(self) -> None:
        facts = self._archer_facts("enablement_appid_invalid", "appid_invalid")
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="The App ID format is invalid. Please provide the correct 32-character App ID.",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm and concise"}},
                account_scope=True,
            )
        self.assertEqual(result.prompt_version, "automation-persona-v29")
        self.assertNotIn("abcdefabcdefabcdefabcdefabcdefab", invoke.call_args.kwargs["user_prompt"])

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
                    "requested_feature": "media_relay",
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

    _AC13085_NOTE = (
        "It's enabled\n"
        "Subject: [Enablement Request] cross platform streaming - Ticket 13085\n"
        "Ticket ID\n13085\n"
        "App ID\n4ba4eb7eae1449b0922909dcb247633d\n"
        "Customer email\nkaber5201@gmail.com\n"
        "Requested feature\nFeature\ncross platform streaming"
    )

    def _ac13085_completion_facts(self) -> dict:
        enriched = {
            "app_id": "4ba4eb7eae1449b0922909dcb247633d",
            "requested_feature": "cross_platform_streaming",
            "requested_feature_label": "cross platform streaming",
            "ticket_id": "13085",
            "account_case_id": "AC-13085",
            "customer_email": "kaber5201@gmail.com",
        }
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="enablement_completed_and_close",
            known_information=enriched,
            source_facts=[sanitize_enablement_completion_note(self._AC13085_NOTE, enriched)],
            resolution_status="completed",
            customer_name="Kaber",
        )
        facts["completion_acknowledgement"] = "patience"
        return facts

    def test_ac13085_completion_note_is_sanitized_before_persona(self) -> None:
        sanitized = sanitize_enablement_completion_note(
            self._AC13085_NOTE,
            {
                "app_id": "4ba4eb7eae1449b0922909dcb247633d",
                "requested_feature": "cross_platform_streaming",
                "requested_feature_label": "cross platform streaming",
                "ticket_id": "13085",
                "account_case_id": "AC-13085",
                "customer_email": "kaber5201@gmail.com",
            },
        )
        for forbidden in (
            "4ba4eb7eae1449b0922909dcb247633d",
            "kaber5201@gmail.com",
            "13085",
            "cross platform streaming",
        ):
            self.assertNotIn(forbidden, sanitized)
        self.assertIn("It's enabled", sanitized)
        self.assertIn("[redacted]", sanitized)

    def test_ac13085_completion_reply_renders_with_canonical_feature_name(self) -> None:
        facts = self._ac13085_completion_facts()
        self.assertEqual(facts["known_information"], {"requested_feature_name": "Media Relay"})
        self.assertNotIn("4ba4eb7eae1449b0922909dcb247633d", facts["source_facts"][0])

        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thanks for your patience. Media Relay is now enabled on your project. "
                "We are archiving this ticket now. If you have any further questions, "
                "please open a new ticket."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )
        self.assertIn("Media Relay", result.content)
        user_prompt = invoke.call_args.kwargs["user_prompt"]
        self.assertIn("Media Relay", user_prompt)
        for forbidden in ("4ba4eb7eae1449b0922909dcb247633d", "kaber5201@gmail.com", "AC-13085"):
            self.assertNotIn(forbidden, user_prompt)

    def test_ac13085_completion_reply_rejects_raw_feature_label_with_canonical_name(self) -> None:
        facts = self._ac13085_completion_facts()
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thanks for your patience. cross platform streaming is now enabled on your project. "
                "We are archiving this ticket now. If you have any further questions, "
                "please open a new ticket."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            with self.assertRaisesRegex(AutomationPersonaError, "automation_persona_forbidden_value"):
                render_automation_reply(
                    reply_facts=facts,
                    persona_assignment={"content": {"instruction": "Warm"}},
                    account_scope=True,
                )

    def test_unknown_feature_completion_reply_allows_customer_wording(self) -> None:
        enriched = {
            "app_id": "abcdefabcdefabcdefabcdefabcdefab",
            "requested_feature": "new_backend_switch",
            "requested_feature_label": "new backand swtich",
            "ticket_id": "13099",
            "account_case_id": "AC-13099",
            "customer_email": "customer@example.com",
        }
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="enablement_completed_and_close",
            known_information=enriched,
            source_facts=[sanitize_enablement_completion_note("It's enabled. Ticket 13099", enriched)],
            resolution_status="completed",
            customer_name="Maya",
        )
        facts["completion_acknowledgement"] = "patience"
        self.assertEqual(facts["known_information"], {})

        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thanks for your patience. The new backand swtich feature is now enabled on your project. "
                "We are archiving this ticket now. If you have any further questions, "
                "please open a new ticket."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )
        self.assertIn("new backand swtich", result.content)

    def test_render_rag_fallback_restates_provided_answer_with_policy(self) -> None:
        facts = {
            "behavior": "rag_fallback_answer",
            "reply_intent": "rag_fallback_answer",
            "provided_answer": (
                "An App ID is the Agora project identifier. Provide the App ID for the Media Relay project."
            ),
            "latest_customer_message": "what is appid?",
            "customer_first_name": "Maya",
        }
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "An App ID is the Agora project identifier. Please provide the App ID for the Media Relay project."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )
        system_prompt = invoke.call_args.kwargs["system_prompt"]
        self.assertIn("restate the provided_answer technical content", system_prompt)
        self.assertIn("Do not invent links", system_prompt)
        self.assertNotIn("For submission_confirmation", system_prompt)
        self.assertNotIn("For request_missing_information", system_prompt)
        user_prompt = invoke.call_args.kwargs["user_prompt"]
        self.assertIn("An App ID is the Agora project identifier", user_prompt)
        self.assertIn("what is appid?", user_prompt)
        self.assertTrue(result.content.startswith("Hi Maya,\n\n"))
        self.assertIn("An App ID is the Agora project identifier", result.content)

    def test_account_persona_selects_only_the_current_intent_policy(self) -> None:
        cases = [
            (
                "missing_information",
                build_automation_reply_facts(
                    behavior="fraud_account",
                    reply_intent="request_missing_information",
                    missing_information=["Account type"],
                ),
                "Could you share the account type? I will continue coordinating once I have it.",
                ("For request_missing_information",),
                ("For submission_confirmation", "For a Fraud handoff"),
            ),
            (
                "submission_confirmation",
                build_automation_reply_facts(
                    behavior="quota",
                    reply_intent="submission_confirmation",
                ),
                "Thanks for sending this. I am reviewing it internally and will keep you posted.",
                ("For submission_confirmation",),
                ("For request_missing_information", "For an Enablement submission"),
            ),
            (
                "enablement_submission",
                build_automation_reply_facts(
                    behavior="enablement",
                    reply_intent="submission_confirmation",
                ),
                (
                    "Thanks for sending this. I am reviewing it internally and will keep you posted. "
                    "Activation may take up to 24 hours, and changes roll out Monday-Friday."
                ),
                ("For submission_confirmation", "For an Enablement submission"),
                ("For request_missing_information",),
            ),
            (
                "fraud_handoff",
                build_automation_reply_facts(
                    behavior="fraud_account",
                    reply_intent="fraud_handoff_confirmation",
                ),
                "I have passed this to the relevant team, and someone will contact you within 24 hours.",
                ("For a Fraud handoff",),
                ("For submission_confirmation", "For request_missing_information"),
            ),
            (
                "suspension_handoff",
                closing_reply_facts(
                    confirmed_email="customer@example.com",
                    customer_name="Maya",
                ),
                (
                    "Thank you for submitting this request. We are reviewing it internally and will get back "
                    "to you within 24 hours."
                ),
                ("For an Account Suspension handoff",),
                ("For submission_confirmation", "For request_missing_information"),
            ),
            (
                "enablement_completion",
                build_automation_reply_facts(
                    behavior="enablement",
                    reply_intent="enablement_completed_and_close",
                ),
                "The feature is already enabled, so I am closing this case now.",
                ("For completed Enablement",),
                ("For submission_confirmation", "For request_missing_information"),
            ),
            (
                "unmatched_account_intent",
                build_automation_reply_facts(
                    behavior="quota",
                    reply_intent="resolution_update",
                ),
                "I reviewed the supplied facts and have an update for you.",
                (),
                ("For submission_confirmation", "For request_missing_information"),
            ),
        ]
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")

        for name, facts, body, expected_policies, excluded_policies in cases:
            with self.subTest(name=name), patch(
                "backend.services.automation_persona.resolve_model_profile", return_value=profile
            ), patch(
                "backend.services.automation_persona.invoke_responses_text",
                return_value=SimpleNamespace(text=body, model_name="persona-model"),
            ) as invoke:
                render_automation_reply(
                    reply_facts=facts,
                    persona_assignment={"content": {"instruction": "Warm"}},
                    account_scope=True,
                )

            system_prompt = invoke.call_args.kwargs["system_prompt"]
            for policy in expected_policies:
                self.assertIn(policy, system_prompt)
            for policy in excluded_policies:
                self.assertNotIn(policy, system_prompt)
            if name == "fraud_handoff":
                self.assertNotIn(
                    "never present an internal team, a job title, or a system as the party responsible",
                    system_prompt,
                )

    def test_non_account_reply_does_not_receive_account_intent_policy(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Thanks for the update. I will keep you posted.",
            model_name="persona-model",
        )
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            render_automation_reply(
                reply_facts={"behavior": "quota", "reply_intent": "submission_confirmation"},
                persona_assignment={"content": {"instruction": "Warm"}},
            )

        self.assertNotIn("For submission_confirmation", invoke.call_args.kwargs["system_prompt"])

    def test_account_persona_generic_instruction_only_requires_applicable_facts(self) -> None:
        facts = {
            "behavior": "rag_fallback_answer",
            "reply_intent": "rag_fallback_answer",
            "provided_answer": "An App ID is the Agora project identifier.",
            "customer_first_name": "Maya",
        }
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(text=facts["provided_answer"], model_name="persona-model")
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )

        system_prompt = invoke.call_args.kwargs["system_prompt"]
        self.assertIn("when supplied and applicable", system_prompt)
        self.assertNotIn(
            "Clearly state the current status, any information the customer needs to provide, and the next step.",
            system_prompt,
        )

    def test_render_rag_fallback_requires_provided_answer(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile):
            with self.assertRaisesRegex(
                AutomationPersonaError, "automation_persona_missing_provided_answer"
            ):
                render_automation_reply(
                    reply_facts={
                        "behavior": "rag_fallback_answer",
                        "reply_intent": "rag_fallback_answer",
                    },
                    persona_assignment={"content": {"instruction": "Warm"}},
                    account_scope=True,
                )

    def test_render_engineer_guided_reply_uses_human_answer_as_only_technical_source(self) -> None:
        facts = {
            "behavior": "engineer_support",
            "reply_intent": "engineer_guided_reply",
            "provided_answer": "Please upgrade to SDK 4.2.2 and retry token renewal.",
            "latest_customer_message": "The token callback does not fire on Android 14.",
            "recent_public_conversation": [
                {"role": "customer", "content": "The token callback does not fire on Android 14."}
            ],
            "subject": "Token callback missing",
            "customer_language": "en",
            "customer_first_name": "Maya",
        }
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Please upgrade to SDK 4.2.2 and retry token renewal.",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm and concise"}},
            )

        system_prompt = invoke.call_args.kwargs["system_prompt"]
        self.assertIn("provided_answer is the only authority", system_prompt)
        self.assertIn("Do not derive or add any diagnosis", system_prompt)
        self.assertEqual(result.prompt_version, "engineer-guided-persona-v3")
        self.assertTrue(result.content.startswith("Hi Maya,\n\n"))
        self.assertIn("SDK 4.2.2", result.content)

    def test_render_engineer_guided_reply_preserves_source_identifier_and_url(self) -> None:
        app_id = "abcdefabcdefabcdefabcdefabcdefab"
        url = "https://docs.agora.io/en/video-calling/get-started/get-started-sdk"
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=f"For App ID {app_id}, follow {url} and retry.",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            result = render_automation_reply(
                reply_facts={
                    "behavior": "engineer_support",
                    "reply_intent": "engineer_guided_reply",
                    "provided_answer": f"For App ID {app_id}, follow <{url}|this guide> and retry.",
                    "customer_first_name": "Maya",
                },
                persona_assignment={"content": {"instruction": "Precise"}},
            )
        self.assertIn(app_id, result.content)
        self.assertIn(url, result.content)

    def test_render_engineer_guided_reply_rejects_invented_identifier(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Use App ID abcdefabcdefabcdefabcdefabcdefab and retry.",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            with self.assertRaisesRegex(
                AutomationPersonaError, "automation_persona_guided_source_value_invented"
            ):
                render_automation_reply(
                    reply_facts={
                        "behavior": "engineer_support",
                        "reply_intent": "engineer_guided_reply",
                        "provided_answer": "Please retry.",
                        "customer_first_name": "Maya",
                    },
                    persona_assignment={"content": {"instruction": "Precise"}},
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

    def test_account_submission_third_person_copy_is_prompt_guided_not_blocked(self) -> None:
        # v22: the ownership clause family is prompt guidance; third-person
        # wording alone no longer blocks publication.
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
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
                account_scope=True,
            )
        self.assertIn("The assigned Support Engineer has started", result.content)

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
        self.assertIn("Hi Jack", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("warm, natural sentences", invoke.call_args.kwargs["system_prompt"])
        self.assertNotIn("Best,\nSid\nSupport Engineer 2", invoke.call_args.kwargs["system_prompt"])

    def test_missing_information_business_content_is_prompt_guided(self) -> None:
        inline_facts = build_account_automation_reply_facts(
            handler="fraud_account",
            action="fraud_account",
            missing_fields=["account_type", "name"],
            collected_fields={},
        )
        validate_account_reply_contract(
            "We are still missing your account type and name. I will continue coordinating the review once you "
            "share them.",
            inline_facts,
        )
        validate_account_reply_contract(
            "We are still missing:\n- Account type\n- Name\nI will continue coordinating the review.",
            inline_facts,
        )
        validate_account_reply_contract(
            "We are still missing your account type and name. We will get back to you within 48 hours.",
            inline_facts,
        )

    def test_fraud_missing_information_is_generated_entirely_by_persona(self) -> None:
        facts = build_account_automation_reply_facts(
            handler="fraud_account",
            action="fraud_account",
            missing_fields=["office_address", "contact_number", "console_configuration"],
            collected_fields={},
            customer_name="Taylor",
        )
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thank you for sharing the information you have so far. Could you please provide the following "
                "information?\n\n- Office address\n- Official contact number\n"
                "- Last known console configuration\n\nAfter you provide this information, I will continue "
                "coordinating the review."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )

        self.assertEqual(
            result.content,
            f"Hi Taylor,\n\n{response.text}",
        )
        self.assertEqual(result.prompt_version, "automation-persona-v29")
        system_prompt = invoke.call_args.kwargs["system_prompt"]
        user_prompt = invoke.call_args.kwargs["user_prompt"]
        self.assertIn("Ask for every missing-information field", system_prompt)
        self.assertIn('"missing_information"', user_prompt)
        self.assertIn("Office address", user_prompt)
        self.assertIn("Official contact number", user_prompt)
        self.assertIn("Last known console configuration", user_prompt)

    def test_fraud_one_or_two_missing_fields_are_generated_inline(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        cases = (
            (
                "fraud_account",
                ["office_address"],
                "Could you please provide your office address?",
            ),
            (
                "account_verification",
                ["office_address", "contact_number"],
                "Could you please provide your office address and official contact number?",
            ),
        )
        for behavior, missing_fields, expected_request in cases:
            response = SimpleNamespace(
                text=(
                    f"Thank you for the information you have already shared. {expected_request} "
                    "After you provide this information, I will continue coordinating the review."
                ),
                model_name="persona-model",
            )
            with self.subTest(behavior=behavior, missing_fields=missing_fields), patch(
                "backend.services.automation_persona.resolve_model_profile", return_value=profile
            ), patch(
                "backend.services.account_ai_execution.invoke_responses_text", return_value=response
            ):
                result = render_automation_reply(
                    reply_facts=build_account_automation_reply_facts(
                        handler=behavior,
                        action=behavior,
                        missing_fields=missing_fields,
                        collected_fields={},
                    ),
                    persona_assignment={"content": {"instruction": "Warm"}},
                    account_scope=True,
                )

            self.assertIn(expected_request, result.content)
            self.assertNotIn("\n- ", result.content)
            self.assertIn(
                "After you provide this information, I will continue coordinating the review.",
                result.content,
            )

    def test_three_missing_fields_rewrite_once_when_bullets_are_missing(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        responses = [
            SimpleNamespace(
                text="Please send your office address, official contact number, and last known console configuration.",
                model_name="persona-model",
            ),
            SimpleNamespace(
                text=(
                    "Please send the following details:\n"
                    "- Office address\n"
                    "- Official contact number\n"
                    "- Last known console configuration"
                ),
                model_name="persona-model",
            ),
        ]
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.account_ai_execution.invoke_responses_text", side_effect=responses
        ) as invoke:
            result = render_automation_reply(
                reply_facts=build_account_automation_reply_facts(
                    handler="fraud_account",
                    action="fraud_account",
                    missing_fields=["office_address", "contact_number", "console_configuration"],
                    collected_fields={},
                ),
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )

        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(result.content, f"Hi Customer,\n\n{responses[1].text}")
        self.assertEqual(result.generation_attempts, 2)
        self.assertEqual(
            result.safety_issue_codes,
            ("automation_persona_missing_information_format_invalid",),
        )
        revision = json.loads(invoke.call_args_list[1].kwargs["user_prompt"])["revision"]
        self.assertEqual(
            revision["issue_codes"],
            ["automation_persona_missing_information_format_invalid"],
        )

    def test_three_missing_fields_fail_closed_after_second_invalid_format(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(text="Please send all missing details.", model_name="persona-model")
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            with self.assertRaises(AutomationPersonaError) as raised:
                render_automation_reply(
                    reply_facts=build_account_automation_reply_facts(
                        handler="fraud_account",
                        action="fraud_account",
                        missing_fields=["office_address", "contact_number", "console_configuration"],
                        collected_fields={},
                    ),
                    persona_assignment={"content": {"instruction": "Warm"}},
                    account_scope=True,
                )

        self.assertEqual(raised.exception.code, "automation_persona_missing_information_format_invalid")
        self.assertEqual(raised.exception.attempt_count, 2)
        self.assertEqual(invoke.call_count, 2)

    def test_customer_first_name_uses_first_token_and_safe_fallback(self) -> None:
        self.assertEqual(customer_first_name("  Jack   Gold  "), "Jack")
        self.assertEqual(customer_first_name("md anisur rahman"), "Md")
        self.assertEqual(customer_first_name("mD anisur rahman"), "MD")
        self.assertEqual(customer_first_name("陈小明"), "陈小明")
        self.assertEqual(customer_first_name("Mary-Jane Watson"), "Mary-Jane")
        self.assertEqual(customer_first_name("customer@example.com"), "Customer")
        self.assertEqual(customer_first_name("Jack<script>"), "Customer")
        self.assertEqual(customer_first_name(""), "Customer")

    def test_render_rewrites_model_generated_greeting_without_stripping_it(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        responses = [
            SimpleNamespace(
                text=(
                    "Hi Jack, I am coordinating the request with our internal team and will keep you updated. "
                    "Activation may take up to 24 hours, and the change window is Monday-Friday."
                ),
                model_name="persona-model",
            ),
            SimpleNamespace(
                text=(
                    "I am coordinating the request with our internal team and will keep you updated. Activation "
                    "may take up to 24 hours, and the change window is Monday-Friday."
                ),
                model_name="persona-model",
            ),
        ]
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", side_effect=responses
        ) as invoke:
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
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(result.safety_issue_codes, ("automation_persona_greeting_forbidden",))

    def test_engineer_guided_reply_rewrites_embedded_greeting(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        responses = [
            SimpleNamespace(
                text=(
                    "Thank you for waiting.\n\n"
                    "Hi Ziling, I understand you are seeing a black screen. Please share the SDK logs."
                ),
                model_name="persona-model",
            ),
            SimpleNamespace(
                text=(
                    "Thank you for waiting.\n\n"
                    "I understand you are seeing a black screen. Please share the SDK logs."
                ),
                model_name="persona-model",
            ),
        ]
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", side_effect=responses
        ) as invoke:
            result = render_automation_reply(
                reply_facts={
                    "behavior": "engineer_support",
                    "reply_intent": "engineer_guided_reply",
                    "provided_answer": "Please share the SDK logs.",
                    "customer_first_name": "Ziling Xie",
                },
                persona_assignment={"content": {"instruction": "Warm"}},
            )

        self.assertEqual(
            result.content,
            "Hi Ziling,\n\nThank you for waiting.\n\n"
            "I understand you are seeing a black screen. Please share the SDK logs.",
        )
        self.assertEqual(result.content.lower().count("hi"), 1)
        self.assertEqual(invoke.call_count, 2)

    def test_engineer_investigation_reply_intent_reuses_guided_contracts(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "The investigation confirmed the missing native library. "
                "Please add abiFilters arm64-v8a in build.gradle and rebuild."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            result = render_automation_reply(
                reply_facts={
                    "behavior": "engineer_support",
                    "reply_intent": "engineer_investigation_reply",
                    "provided_answer": (
                        "Conclusion: APK is missing Agora arm64-v8a native libraries.\n"
                        "Suggested resolution: Add abiFilters arm64-v8a in build.gradle."
                    ),
                    "customer_first_name": "Ziling Xie",
                },
                persona_assignment={"content": {"instruction": "Warm"}},
            )
        self.assertTrue(result.content.startswith("Hi Ziling,"))
        self.assertEqual(result.prompt_version, "engineer-investigation-persona-v1")

    def test_engineer_investigation_reply_requires_provided_answer(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile):
            with self.assertRaisesRegex(AutomationPersonaError, "automation_persona_missing_provided_answer"):
                render_automation_reply(
                    reply_facts={
                        "behavior": "engineer_support",
                        "reply_intent": "engineer_investigation_reply",
                        "customer_first_name": "Ziling Xie",
                    },
                    persona_assignment={"content": {"instruction": "Warm"}},
                )

    def test_engineer_investigation_reply_rejects_invented_identifier(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Please check ticket 99999 for the fix.",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            with self.assertRaisesRegex(AutomationPersonaError, "automation_persona_guided_source_value_invented"):
                render_automation_reply(
                    reply_facts={
                        "behavior": "engineer_support",
                        "reply_intent": "engineer_investigation_reply",
                        "provided_answer": "Add abiFilters arm64-v8a in build.gradle.",
                        "customer_first_name": "Ziling Xie",
                    },
                    persona_assignment={"content": {"instruction": "Warm"}},
                )

    def test_engineer_investigation_reply_requires_customer_name(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile):
            self.assertRaisesRegex(
                AutomationPersonaError,
                "automation_persona_guided_customer_name_missing",
                render_automation_reply,
                reply_facts={
                    "behavior": "engineer_support",
                    "reply_intent": "engineer_investigation_reply",
                    "provided_answer": "Please retry after the packaging fix.",
                    "customer_first_name": "customer@example.com",
                },
                persona_assignment={"content": {"instruction": "Warm"}},
            )

    def test_engineer_guided_reply_requires_customer_name(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile):
            with self.assertRaisesRegex(
                AutomationPersonaError,
                "automation_persona_guided_customer_name_missing",
            ):
                render_automation_reply(
                    reply_facts={
                        "behavior": "engineer_support",
                        "reply_intent": "engineer_guided_reply",
                        "provided_answer": "Please retry.",
                        "customer_first_name": "",
                    },
                    persona_assignment={"content": {"instruction": "Warm"}},
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

    def test_fraud_handoff_wording_is_prompt_guided(self) -> None:
        # v22: the will+contact+24h same-clause shape is gone; natural
        # phrasing (and even a missing 24-hour mention) passes validation —
        # point coverage moved to the prompt and the live-scenario checks.
        facts = {
            "behavior": "fraud_account",
            "reply_intent": "fraud_handoff_confirmation",
        }
        for valid_reply in (
            "The relevant team will contact you within 24 hours.",
            "Hi Customer\n\nThe relevant team will contact you within 24 hours.",
            "Our fraud specialists will contact you within 24 hours.",
            "The relevant team has received the request. They will follow up within 24 hours.",
            "We've sent this to our fraud team, who will be in touch within 24 hours.",
            "Thanks for sending this over - I've looped in the relevant team, and someone from their side will "
            "reach out to you within 24 hours, so there's nothing you need to chase on your end.",
            "We received your request and will review it.",
            "The relevant team will not contact you within 24 hours.",
            "Will the relevant team contact you within 24 hours?",
            "Our fraud specialists will contact you next week.",
            "The relevant team has received the request and will follow up.",
        ):
            with self.subTest(valid_reply=valid_reply):
                validate_account_reply_contract(valid_reply, facts)

    def test_safety_feedback_rewrites_once_then_returns_valid_body(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        responses = [
            SimpleNamespace(text="Media Relay will be enabled tomorrow.", model_name="persona-model"),
            SimpleNamespace(
                text="Media Relay is already enabled on your project. I'm closing this case now.",
                model_name="persona-model",
            ),
        ]
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", side_effect=responses
        ) as invoke:
            result = render_automation_reply(
                reply_facts={
                    "behavior": "enablement",
                    "reply_intent": "enablement_completed_and_close",
                },
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )

        self.assertIn("Media Relay is already enabled", result.content)
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(result.generation_attempts, 2)
        self.assertEqual(
            result.safety_issue_codes,
            ("automation_persona_completion_contract_failed_enabled_state",),
        )
        self.assertIn("previous_candidate", invoke.call_args_list[1].kwargs["user_prompt"])

    def test_safety_validation_exhaustion_preserves_contract_code(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Media Relay will be enabled tomorrow.",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            with self.assertRaises(AutomationPersonaError) as raised:
                render_automation_reply(
                    reply_facts={
                        "behavior": "enablement",
                        "reply_intent": "enablement_completed_and_close",
                    },
                    persona_assignment={"content": {"instruction": "Warm"}},
                    account_scope=True,
                )

        self.assertEqual(raised.exception.code, "automation_persona_completion_contract_failed_enabled_state")
        self.assertEqual(raised.exception.attempt_count, 2)
        self.assertEqual(invoke.call_count, 2)

    def test_enablement_submission_wording_is_prompt_guided(self) -> None:
        # Business completeness is prompt-owned; code validation keeps no
        # SLA or weekday presence detector.
        facts = {
            "behavior": "enablement",
            "reply_intent": "submission_confirmation",
        }
        validate_account_reply_contract("We are reviewing the request.", facts)
        validate_account_reply_contract(
            "I am coordinating activation and will keep you updated. It may take up to 24 hours, and the change "
            "window is Monday-Friday.",
            facts,
        )
        validate_account_reply_contract(
            "Activation will not happen within 24 hours; changes occur Monday-Friday.",
            facts,
        )

    def test_enablement_submission_soft_omission_does_not_rewrite(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thank you for providing the requested information. I am reviewing the request with our internal "
                "team and will keep you updated."
            ),
            model_name="persona-model",
        )
        facts = build_account_automation_reply_facts(
            handler="enablement",
            action="enablement",
            missing_fields=[],
            collected_fields={"requested_feature": "media_relay"},
            submitted=True,
        )

        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm and precise."}},
                account_scope=True,
            )

        self.assertEqual(result.content, f"Hi Customer,\n\n{response.text}")
        self.assertEqual(result.prompt_version, "automation-persona-v29")
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(result.generation_attempts, 1)

    def test_enablement_submission_pass_preserves_persona_body_exactly(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        facts = build_account_automation_reply_facts(
            handler="enablement",
            action="enablement",
            missing_fields=[],
            collected_fields={"requested_feature": "media_relay"},
            submitted=True,
        )
        bodies = (
            "I am reviewing the request and will keep you updated. Activation may take up to 24 hours, and "
            "the change window is Monday-Friday.",
            "I've logged the request. Changes run Monday-Friday and activation can take up to 24 hours; I'll keep "
            "you posted.",
        )

        for text in bodies:
            with self.subTest(text=text):
                response = SimpleNamespace(text=text, model_name="persona-model")
                with patch(
                    "backend.services.automation_persona.resolve_model_profile",
                    return_value=profile,
                ), patch(
                    "backend.services.account_ai_execution.invoke_responses_text",
                    return_value=response,
                ) as invoke:
                    result = render_automation_reply(
                        reply_facts=facts,
                        persona_assignment={"content": {"instruction": "Warm and precise."}},
                        account_scope=True,
                    )

                self.assertEqual(result.content, f"Hi Customer,\n\n{text}")
                self.assertEqual(invoke.call_count, 1)

    def test_enablement_submission_incomplete_body_is_not_semantically_blocked(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        facts = build_account_automation_reply_facts(
            handler="enablement",
            action="enablement",
            missing_fields=[],
            collected_fields={"requested_feature": "media_relay"},
            submitted=True,
        )
        response = SimpleNamespace(
            text="I am reviewing the request and will keep you updated.",
            model_name="persona-model",
        )
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm and precise."}},
                account_scope=True,
            )

        self.assertEqual(result.content, f"Hi Customer,\n\n{response.text}")
        self.assertEqual(invoke.call_count, 1)

    def test_suspension_replies_reject_affirmative_close_claims_only(self) -> None:
        facts = {
            "behavior": "account_suspension",
            "reply_intent": "account_suspension_contact_confirmation_request",
        }
        # v22: the email/interrogative/24h sentence shapes are prompt-guided;
        # even legacy close/reopen wording passes when phrased negatively.
        validate_account_reply_contract(
            "Which email is most convenient for you? Should we use the email on this ticket? "
            "The relevant team will contact you within 24 hours. We will not close this ticket, and you do not "
            "need to reopen it.",
            facts,
        )
        for apostrophe in ("'", "‘", "’", "ʼ", "＇"):
            validate_account_reply_contract(
                f"We won{apostrophe}t close this ticket, and you don{apostrophe}t need to reopen it.",
                facts,
            )
        validate_account_reply_contract("Please share an email address.", facts)
        for invalid_reply in (
            "Which email is best for you? The relevant team will contact you within 24 hours. "
            "This ticket will close after the handoff.",
            "Which email is best for you? We have closed this ticket; you can reopen it any time.",
            "Which email is best for you? The case is being archived.",
        ):
            with self.subTest(invalid_reply=invalid_reply):
                with self.assertRaisesRegex(
                    AutomationPersonaError, "automation_persona_suspension_close_claim_forbidden"
                ):
                    validate_account_reply_contract(invalid_reply, facts)

    def test_suspension_closing_contract_accepts_natural_handoff_phrasing(self) -> None:
        # v23: commitment phrasing (even a missing or negated commitment) is
        # prompt guidance — only an affirmative close/reopen claim blocks.
        facts = {
            "behavior": "account_suspension",
            "reply_intent": "account_suspension_handoff_and_close",
        }
        for valid_reply in (
            "Thanks for reaching out. Someone will be in touch. This should happen within 24 hours.",
            "Someone from the team is expected to reach out within the next 24 hours.",
            "The team will contact you in 24 hours.",
            "Someone should reach out to you in 24 hours with an update.",
            "The relevant team will get back to you within 24h.",
            "You do not need to reopen the ticket; we will contact you within 24 hours.",
            "We will not contact you within 24 hours.",
            "Will someone contact you within 24 hours?",
            "Thanks for your patience. Someone will follow up with you soon.",
        ):
            with self.subTest(valid_reply=valid_reply):
                validate_account_reply_contract(valid_reply, facts)
        for invalid_reply in (
            "This ticket will close after the handoff. We will contact you within 24 hours.",
            "You can reopen the ticket at any time. We will contact you within 24 hours.",
        ):
            with self.subTest(invalid_reply=invalid_reply):
                with self.assertRaisesRegex(
                    AutomationPersonaError, "automation_persona_suspension_close_claim_forbidden"
                ):
                    validate_account_reply_contract(invalid_reply, facts)

    def test_suspension_missing_commitment_is_not_semantically_blocked(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Thank you for submitting this request. It is under internal review.",
            model_name="persona-model",
        )
        facts = closing_reply_facts(
            confirmed_email="customer@example.com",
            customer_name="Maya",
        )

        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm and precise."}},
                account_scope=True,
            )

        self.assertEqual(result.content, f"Hi Maya,\n\n{response.text}")
        self.assertEqual(invoke.call_count, 1)

    def test_suspension_duplicate_commitment_is_published_unchanged(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thank you for submitting this request. We are reviewing it internally and will get back to "
                "you within 24 hours. We will get back to you within 24 hours."
            ),
            model_name="persona-model",
        )
        with patch(
            "backend.services.automation_persona.resolve_model_profile", return_value=profile
        ), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=closing_reply_facts(
                    confirmed_email="customer@example.com",
                    customer_name="Maya",
                ),
                persona_assignment={"content": {"instruction": "Warm and precise."}},
                account_scope=True,
            )

        self.assertEqual(result.content, f"Hi Maya,\n\n{response.text}")
        self.assertEqual(result.content.count("within 24 hours"), 2)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(result.safety_issue_codes, ())

    def test_suspension_curly_apostrophe_passes_without_worker_repair(self) -> None:
        # p2-142: the brief customer-facing three-point reply (thanks /
        # internal review / we will get back within 24 hours) passes as-is.
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thank you for submitting this request. We are reviewing it internally and we’ll get "
                "back to you within 24 hours."
            ),
            model_name="persona-model",
        )
        facts = closing_reply_facts(
            confirmed_email="customer@example.com",
            customer_name="Maya",
        )

        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm and precise."}},
                account_scope=True,
            )

        self.assertEqual(
            result.content,
            f"Hi Maya,\n\n{response.text}",
        )
        self.assertEqual(result.content.count("24 hours"), 1)
        self.assertEqual(result.safety_status, "passed")
        self.assertEqual(result.generation_attempts, 1)
        self.assertNotIn("suspension", result.content.lower())
        self.assertEqual(invoke.call_count, 1)
        system_prompt = invoke.call_args.kwargs["system_prompt"]
        self.assertIn("thank the customer for submitting the request", system_prompt)
        self.assertIn("reviewed internally", system_prompt)
        self.assertIn("we will get back to them within 24 hours", system_prompt)
        self.assertNotIn("handed to the relevant team", system_prompt)

    def test_suspension_close_claim_is_never_modified_or_published(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="We have closed this ticket and you can reopen it any time.",
            model_name="persona-model",
        )
        facts = closing_reply_facts(
            confirmed_email="customer@example.com",
            customer_name="Maya",
        )

        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            with self.assertRaisesRegex(
                AutomationPersonaError, "automation_persona_suspension_close_claim_forbidden"
            ):
                render_automation_reply(
                    reply_facts=facts,
                    persona_assignment={"content": {"instruction": "Warm and precise."}},
                    account_scope=True,
                )

        self.assertEqual(invoke.call_count, 2)

    def test_completion_contract_keeps_future_tense_floor(self) -> None:
        facts = {
            "behavior": "enablement",
            "reply_intent": "enablement_completed_and_close",
            "completion_acknowledgement": "additional_information",
        }
        # v22: positive enabled/closing requirements are prompt guidance, so
        # even a reply that omits them validates; only misleading future
        # claims (will be enabled / will be archived without an immediacy
        # marker in the same clause) still block.
        for valid_reply in (
            "Thanks for providing the additional information. We have now enabled Media Relay. "
            "We will mark this case as archived now. If you have any further questions or concerns, "
            "please feel free to open a new ticket.",
            "We appreciate your patience. Media Relay is now enabled. We are archiving this case now. "
            "If you need anything else, please open a new ticket.",
            "Thanks for waiting on this one - I'm happy to confirm the feature is already enabled on your "
            "project, so you should be all set. I'm closing this case now, but if any questions come up "
            "later, feel free to open a new ticket and we'll take it from there.",
            "Media Relay is enabled. This case is archived.",
            "Thanks for providing the additional information. This case is archived. If you have questions, please open a new ticket.",
            "Thanks for providing the additional information. Media Relay is enabled. If you have questions, please open a new ticket.",
            "Thanks for providing the additional information. Media Relay is not enabled. This case is archived. If you have questions, please open a new ticket.",
            "Thanks for providing the additional information. Will Media Relay be enabled? This case is archived. If you have questions, please open a new ticket.",
        ):
            with self.subTest(valid_reply=valid_reply):
                normalized, close = validate_account_reply_contract(
                    valid_reply, facts, close_after_publish=True
                )
                self.assertTrue(close)
                self.assertEqual(normalized["reply_intent"], "enablement_completed_and_close")
        for invalid_reply, failure_code in (
            (
                "Thanks for providing the additional information. Media Relay will be enabled tomorrow. This case will be archived tomorrow. If you have questions, please open a new ticket.",
                "completion_contract_failed_enabled_state",
            ),
            (
                "Thanks for providing the additional information. Media Relay is enabled. This case will be archived tomorrow. If you have questions, please open a new ticket.",
                "completion_contract_failed_archive",
            ),
        ):
            with self.subTest(invalid_reply=invalid_reply, failure_code=failure_code):
                with self.assertRaisesRegex(AutomationPersonaError, failure_code):
                    validate_account_reply_contract(
                        invalid_reply,
                        facts,
                        close_after_publish=True,
                    )

    def test_completion_prompt_carries_first_person_style_reference(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thanks for waiting on this one - I'm happy to confirm Media Relay is already enabled on your "
                "project, so you should be all set. I'm closing this case now, but if any questions come up "
                "later, feel free to open a new ticket and we'll take it from there."
            ),
            model_name="persona-model",
        )
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="enablement_completed_and_close",
            known_information={"requested_feature": "media_relay"},
            customer_name="Ziling",
        )

        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm and precise."}},
                account_scope=True,
            )

        self.assertTrue(result.content.startswith("Hi Ziling,\n\n"))
        self.assertEqual(result.prompt_version, "automation-persona-v29")
        system_prompt = invoke.call_args.kwargs["system_prompt"]
        self.assertIn("already enabled", system_prompt)
        self.assertIn("closing this case", system_prompt)
        self.assertIn("open a new ticket", system_prompt)
        self.assertIn("match the tone", system_prompt)
        self.assertIn("speak in first person", system_prompt)
        self.assertIn("required content", system_prompt)

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

    def test_submission_reply_ownership_wording_is_prompt_guided(self) -> None:
        # v22: the internal-team delegation ban is prompt guidance; the
        # wording alone no longer blocks publication.
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
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
                account_scope=True,
            )
        self.assertIn("The internal team will follow up", result.content)

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

    def test_resolve_customer_greeting_name_falls_through_invalid_candidates(self) -> None:
        from backend.services.automation_persona import resolve_customer_greeting_name

        cases = (
            # (author, case, requester, expected)
            ("Ada Lovelace", "Grace Hopper", "cx@example.com", "Ada"),
            ("Ada Lovelace", None, None, "Ada"),
            ("cx@example.com", "Grace Hopper", None, "Grace"),  # invalid author -> case name
            ("", "Grace Hopper", None, "Grace"),
            ("customer", "Grace Hopper", None, "Grace"),  # placeholder author -> case name
            ("https://x.example", None, "Ziling Xie", "Ziling"),  # invalid author -> requester
            ("cx@example.com", "unknown", "TK-123", "Customer"),  # all invalid -> fallback
            (None, None, None, "Customer"),
        )
        for author, case_name, requester, expected in cases:
            with self.subTest(author=author, case=case_name, requester=requester):
                self.assertEqual(
                    resolve_customer_greeting_name(
                        latest_customer_author_name=author,
                        case_customer_name=case_name,
                        requester_name=requester,
                    ),
                    expected,
                )

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

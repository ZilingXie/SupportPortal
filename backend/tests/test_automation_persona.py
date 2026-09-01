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
from backend.services.detailed_invoice_field_extractor import extract_detailed_invoice_fields
from backend.services.billing_automation import build_billing_automation_result


class AutomationPersonaTests(unittest.TestCase):
    def _archer_facts(self, intent: str, outcome: str) -> dict:
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent=intent,
            known_information={
                "app_id": "abcdefabcdefabcdefabcdefabcdefab",
                "requested_feature": "media_relay",
                "requested_feature_label": "media rele",
                "archer_outcome": outcome,
                **(
                    {"region": "oversea", "max_subscribe_load": 50}
                    if outcome == "enabled"
                    else {}
                ),
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
                "max_subscribe_load": 50,
                "region": "oversea",
                "requested_feature_name": "Media Relay",
            },
        )
        self.assertEqual(facts["customer_first_name"], "Ada")
        self.assertIn("abcdefabcdefabcdefabcdefabcdefab", facts["_forbidden_values"])

    def test_archer_success_contract_requires_current_configuration_and_closure(self) -> None:
        facts = self._archer_facts("enablement_archer_enabled", "enabled")
        valid = (
            "Thank you for your patience. Media Relay is already enabled for the oversea region with a maximum "
            "subscribe load of 50. This case will be archived now. If you have further questions, you can open "
            "a new ticket."
        )
        normalized, close = validate_account_reply_contract(valid, facts, close_after_publish=True)
        self.assertTrue(close)
        self.assertEqual(normalized["reply_intent"], "enablement_archer_enabled")
        for invalid in (
            valid.replace("already enabled", "will be enabled"),
            valid.replace("oversea region", "configured region"),
            valid.replace("subscribe load of 50", "subscribe load"),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(AutomationPersonaError):
                    validate_account_reply_contract(invalid, facts, close_after_publish=True)

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

    def test_archer_persona_prompt_keeps_v19_and_excludes_appid(self) -> None:
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
        self.assertEqual(result.prompt_version, "automation-persona-v19")
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
            "provided_answer": "An App ID identifies an Agora project created in Console.",
            "customer_first_name": "Maya",
        }
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="I checked this for you: an App ID identifies an Agora project created in Console.",
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
        user_prompt = invoke.call_args.kwargs["user_prompt"]
        self.assertIn("An App ID identifies an Agora project created in Console.", user_prompt)
        self.assertTrue(result.content.startswith("Hi Maya\n\n"))
        self.assertIn("an App ID identifies an Agora project", result.content)

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
        self.assertEqual(result.prompt_version, "engineer-guided-persona-v2")
        self.assertTrue(result.content.startswith("Hi Maya\n\n"))
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

        self.assertEqual(result.content, f"Hi Jack\n\n{response.text}")
        self.assertEqual(result.model, "persona-model")
        self.assertIn('"missing_information"', invoke.call_args.kwargs["user_prompt"])
        self.assertIn("Warm", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("Do not write a greeting, signoff", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("Hi Jack", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("warm, natural sentences", invoke.call_args.kwargs["system_prompt"])
        self.assertNotIn("Best,\nSid\nSupport Engineer 2", invoke.call_args.kwargs["system_prompt"])

    def test_missing_information_format_contract_uses_inline_or_bullets(self) -> None:
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
        with self.assertRaisesRegex(
            AutomationPersonaError,
            "automation_persona_missing_information_format_failed",
        ):
            validate_account_reply_contract(
                "We are still missing:\n- Account type\n- Name\nI will continue coordinating the review.",
                inline_facts,
            )

        bullet_facts = build_account_automation_reply_facts(
            handler="fraud_account",
            action="fraud_account",
            missing_fields=["account_type", "name", "office_address"],
            collected_fields={},
        )
        validate_account_reply_contract(
            "I am still missing the following details:\n\n- Account type\n- Name\n- Office address\n\n"
            "I will continue coordinating the review once you share them.",
            bullet_facts,
        )
        with self.assertRaisesRegex(
            AutomationPersonaError,
            "automation_persona_missing_information_format_failed",
        ) as raised:
            validate_account_reply_contract(
                "I am still missing the following details:\n1. Account type\n2. Name\n3. Office address\n"
                "I will continue coordinating the review once you share them.",
                bullet_facts,
            )
        self.assertEqual(raised.exception.detail, "numbered_list_detected")

    def test_fraud_missing_information_is_assembled_deterministically(self) -> None:
        facts = build_account_automation_reply_facts(
            handler="fraud_account",
            action="fraud_account",
            missing_fields=["office_address", "contact_number", "console_configuration"],
            collected_fields={},
            customer_name="Taylor",
        )
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Thank you for sharing the information you have so far.",
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
            "Hi Taylor\n\n"
            "Thank you for sharing the information you have so far.\n\n"
            "Could you please provide the following information?\n\n"
            "- Office address\n"
            "- Official contact number\n"
            "- Last known console configuration\n\n"
            "After you provide this information, I will continue coordinating the review.",
        )
        self.assertEqual(result.prompt_version, "automation-persona-v19")
        system_prompt = invoke.call_args.kwargs["system_prompt"]
        user_prompt = invoke.call_args.kwargs["user_prompt"]
        self.assertIn("application will append the exact missing-information request", system_prompt)
        self.assertIn('"missing_information_count": 3', user_prompt)
        self.assertNotIn('"missing_information"', user_prompt)
        self.assertNotIn("Office address", user_prompt)
        self.assertNotIn("Official contact number", user_prompt)
        self.assertNotIn("Last known console configuration", user_prompt)

    def test_fraud_one_or_two_missing_fields_are_assembled_inline(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Thank you for the information you have already shared.",
            model_name="persona-model",
        )
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

    def test_fraud_missing_information_retries_invalid_preambles(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        responses = [
            SimpleNamespace(text="1. Thank you for the details.", model_name="persona-model"),
            SimpleNamespace(text="We still need your Office address.", model_name="persona-model"),
            SimpleNamespace(text="Could you also share your phone number?", model_name="persona-model"),
            SimpleNamespace(
                text="Thank you for sharing the information you have so far.",
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

        self.assertEqual(invoke.call_count, 4)
        self.assertNotIn("1. Thank you", result.content)
        self.assertNotIn("phone number", result.content)
        self.assertEqual(result.content.count("Office address"), 1)
        self.assertEqual(result.content.count("Official contact number"), 1)
        self.assertEqual(result.content.count("Last known console configuration"), 1)

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
            "Hi Jack\n\nI am coordinating the request with our internal team and will keep you updated. Activation may "
            "take up to 24 hours, and the change window is Monday-Friday.",
        )

    def test_engineer_guided_reply_removes_greeting_after_waiting_preamble(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thank you for waiting.\n\n"
                "Hi Ziling, I understand you are seeing a black screen. Please share the SDK logs."
            ),
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
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
            "Hi Ziling\n\nThank you for waiting.\n\n"
            "I understand you are seeing a black screen. Please share the SDK logs.",
        )
        self.assertEqual(result.content.lower().count("hi"), 1)

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

        self.assertEqual(result.content, "Hi Customer\n\nThe request is complete.")

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
        validate_account_reply_contract(
            "Hi Customer\n\nThe relevant team will contact you within 24 hours.",
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

        for paraphrase in (
            "Our fraud specialists will contact you within 24 hours.",
            "The relevant team has received the request. They will follow up within 24 hours.",
            "We've sent this to our fraud team, who will be in touch within 24 hours.",
        ):
            with self.subTest(paraphrase=paraphrase):
                with self.assertRaisesRegex(AutomationPersonaError, "fraud_handoff_contract_failed"):
                    validate_account_reply_contract(paraphrase, facts)

    def test_fraud_handoff_validation_retries_then_returns_fourth_valid_body(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        responses = [
            SimpleNamespace(text="Our fraud specialists will contact you within 24 hours.", model_name="persona-model"),
            SimpleNamespace(text="They will follow up within 24 hours.", model_name="persona-model"),
            SimpleNamespace(text="Will the relevant team contact you within 24 hours?", model_name="persona-model"),
            SimpleNamespace(text="The relevant team will contact you within 24 hours.", model_name="persona-model"),
        ]
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", side_effect=responses
        ) as invoke:
            result = render_automation_reply(
                reply_facts={"behavior": "fraud_account", "reply_intent": "fraud_handoff_confirmation"},
                persona_assignment={"content": {"instruction": "Warm"}},
                account_scope=True,
            )

        self.assertEqual(
            result.content,
            "Hi Customer\n\nThe relevant team will contact you within 24 hours.",
        )
        self.assertEqual(invoke.call_count, 4)

    def test_fraud_handoff_validation_exhaustion_preserves_contract_code(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text="Our fraud specialists will contact you within 24 hours.",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            with self.assertRaises(AutomationPersonaError) as raised:
                render_automation_reply(
                    reply_facts={"behavior": "fraud_account", "reply_intent": "fraud_handoff_confirmation"},
                    persona_assignment={"content": {"instruction": "Warm"}},
                    account_scope=True,
                )

        self.assertEqual(raised.exception.code, "automation_persona_fraud_handoff_contract_failed")
        self.assertEqual(raised.exception.attempt_count, 4)
        self.assertEqual(invoke.call_count, 4)

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

    def test_enablement_submission_deterministically_completes_omitted_contract(self) -> None:
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

        self.assertIn(
            "Activation may take up to 24 hours, and the change window is Monday-Friday.",
            result.content,
        )
        self.assertEqual(result.prompt_version, "automation-persona-v19")
        self.assertEqual(invoke.call_count, 1)

    def test_enablement_submission_deterministic_completion_preserves_existing_clauses(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        facts = build_account_automation_reply_facts(
            handler="enablement",
            action="enablement",
            missing_fields=[],
            collected_fields={"requested_feature": "media_relay"},
            submitted=True,
        )
        cases = (
            (
                "I am reviewing the request and will keep you updated. Activation may take up to 24 hours.",
                "Activation may take up to 24 hours.",
                "The change window is Monday-Friday.",
            ),
            (
                "I am reviewing the request and will keep you updated. The change window is Monday-Friday.",
                "Activation may take up to 24 hours.",
                "The change window is Monday-Friday.",
            ),
            (
                "I am reviewing the request and will keep you updated. Activation may take up to 24 hours, and "
                "the change window is Monday-Friday.",
                "Activation may take up to 24 hours",
                "the change window is Monday-Friday",
            ),
        )

        for text, sla_clause, window_clause in cases:
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

                self.assertEqual(result.content.count(sla_clause), 1)
                self.assertEqual(result.content.count(window_clause), 1)
                self.assertEqual(invoke.call_count, 1)

    def test_enablement_submission_deterministic_completion_rejects_nonpositive_contract(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        facts = build_account_automation_reply_facts(
            handler="enablement",
            action="enablement",
            missing_fields=[],
            collected_fields={"requested_feature": "media_relay"},
            submitted=True,
        )
        responses = (
            "I am reviewing the request and will keep you updated. Activation will not happen within 24 hours.",
            "I am reviewing the request and will keep you updated. Are weekdays the change window?",
        )

        for text in responses:
            with self.subTest(text=text):
                response = SimpleNamespace(text=text, model_name="persona-model")
                with patch(
                    "backend.services.automation_persona.resolve_model_profile",
                    return_value=profile,
                ), patch(
                    "backend.services.account_ai_execution.invoke_responses_text",
                    return_value=response,
                ) as invoke:
                    with self.assertRaisesRegex(
                        AutomationPersonaError,
                        "automation_persona_enablement_submission_contract_failed",
                    ) as raised:
                        render_automation_reply(
                            reply_facts=facts,
                            persona_assignment={"content": {"instruction": "Warm and precise."}},
                            account_scope=True,
                        )

                self.assertEqual(raised.exception.attempt_count, 4)
                self.assertEqual(invoke.call_count, 4)

    def test_suspension_contact_contract_requires_email_close_and_reopen_terms(self) -> None:
        facts = {
            "behavior": "account_suspension",
            "reply_intent": "account_suspension_contact_confirmation_request",
        }
        validate_account_reply_contract(
            "Which email is most convenient for you? Should we use the email on this ticket? "
            "The relevant team will contact you within 24 hours. The ticket will close after handoff, "
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

    def test_completion_contract_requires_contextual_acknowledgement_and_full_close_guidance(self) -> None:
        facts = {
            "behavior": "enablement",
            "reply_intent": "enablement_completed_and_close",
            "completion_acknowledgement": "additional_information",
        }
        validate_account_reply_contract(
            "Thanks for providing the additional information. We have now enabled Media Relay. "
            "We will mark this case as archived now. If you have any further questions or concerns, "
            "please feel free to open a new ticket.",
            facts,
            close_after_publish=True,
        )
        for invalid_reply, failure_code in (
            (
                "Media Relay is enabled. This case is archived. If you have questions, please open a new ticket.",
                "completion_contract_failed_acknowledgement",
            ),
            (
                "Thanks for providing the additional information. This case is archived. If you have questions, please open a new ticket.",
                "completion_contract_failed_enabled_state",
            ),
            (
                "Thanks for providing the additional information. Media Relay is enabled. If you have questions, please open a new ticket.",
                "completion_contract_failed_archive",
            ),
            (
                "Thanks for providing the additional information. Media Relay is enabled. This case is archived.",
                "completion_contract_failed_new_ticket_guidance",
            ),
            (
                "Thanks for providing the additional information. Media Relay is not enabled. This case is archived. If you have questions, please open a new ticket.",
                "completion_contract_failed_enabled_state",
            ),
            (
                "Thanks for providing the additional information. Will Media Relay be enabled? This case is archived. If you have questions, please open a new ticket.",
                "completion_contract_failed_enabled_state",
            ),
            (
                "Thanks for providing the additional information. Media Relay will be enabled tomorrow. This case will be archived tomorrow. If you have questions, please open a new ticket.",
                "completion_contract_failed_enabled_state",
            ),
            (
                "Thanks for providing the additional information. Media Relay is not enabled. Media Relay is enabled. This case is archived. If you have questions, please open a new ticket.",
                "completion_contract_failed_enabled_state",
            ),
            (
                "Thanks for providing the additional information. Media Relay is enabled. This case will be archived tomorrow. This case is archived now. If you have questions, please open a new ticket.",
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

    def test_completion_patience_contract_rejects_invented_additional_information(self) -> None:
        facts = {
            "behavior": "enablement",
            "reply_intent": "enablement_completed_and_close",
            "completion_acknowledgement": "patience",
        }
        for valid_reply in (
            "We appreciate your patience. Media Relay is now enabled. We are archiving this case now. "
            "If you need anything else, please open a new ticket.",
            "Thank you for waiting. Media Relay is now enabled. We are archiving this case now. "
            "If you need further help, you can open a new ticket.",
        ):
            with self.subTest(valid_reply=valid_reply):
                validate_account_reply_contract(
                    valid_reply,
                    facts,
                    close_after_publish=True,
                )
        for invalid_reply, failure_code in (
            (
                "Thank you for your patience and for providing the additional information. Media Relay is now "
                "enabled. We are archiving this case now. If you have further questions, you can open a new ticket.",
                "completion_contract_failed_acknowledgement",
            ),
            (
                "I don't appreciate your patience. Media Relay is now enabled. We are archiving this case now. "
                "If you need anything else, please open a new ticket.",
                "completion_contract_failed_acknowledgement",
            ),
            (
                "We appreciate your patience. Media Relay isn't enabled. We are archiving this case now. "
                "If you need anything else, please open a new ticket.",
                "completion_contract_failed_enabled_state",
            ),
            (
                "We appreciate your patience. Media Relay is now enabled. We are archiving this case now. "
                "If you don't need anything else, please open a new ticket.",
                "completion_contract_failed_new_ticket_guidance",
            ),
        ):
            with self.subTest(invalid_reply=invalid_reply, failure_code=failure_code):
                with self.assertRaisesRegex(AutomationPersonaError, failure_code):
                    validate_account_reply_contract(
                        invalid_reply,
                        facts,
                        close_after_publish=True,
                    )

    def test_completion_prompt_carries_additional_information_archive_and_new_ticket_policy(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(
            text=(
                "Thanks for providing the additional information. We have now enabled Media Relay. "
                "We will mark this case as archived now. If you have any further questions or concerns, "
                "please feel free to open a new ticket."
            ),
            model_name="persona-model",
        )
        facts = build_automation_reply_facts(
            behavior="enablement",
            reply_intent="enablement_completed_and_close",
            known_information={"requested_feature": "media_relay"},
            customer_name="Ziling",
        )
        facts["completion_acknowledgement"] = "additional_information"

        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm and precise."}},
                account_scope=True,
            )

        self.assertTrue(result.content.startswith("Hi Ziling\n\n"))
        self.assertEqual(result.prompt_version, "automation-persona-v19")
        system_prompt = invoke.call_args.kwargs["system_prompt"]
        self.assertIn("providing the additional information", system_prompt)
        self.assertIn("archived now", system_prompt)
        self.assertIn("open a new ticket", system_prompt)
        self.assertIn("need anything else", system_prompt)

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

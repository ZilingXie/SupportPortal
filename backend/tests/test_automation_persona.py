from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.automation_persona import (
    AutomationPersonaError,
    build_automation_reply_facts,
    customer_first_name,
    extract_automation_resolution_facts,
    render_automation_reply,
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
            text="We have submitted your Media Relay request for internal review.",
            model_name="persona-model",
        )
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ) as invoke:
            result = render_automation_reply(
                reply_facts=facts,
                persona_assignment={"content": {"instruction": "Warm", "signature": "Best,\nSid"}},
            )

        user_prompt = invoke.call_args.kwargs["user_prompt"]
        system_prompt = invoke.call_args.kwargs["system_prompt"]
        self.assertIn("Media Relay", user_prompt)
        self.assertNotIn("abcdefabcdefabcdefabcdefabcdefab", user_prompt)
        self.assertNotIn("channel media rele", user_prompt)
        self.assertIn("Do not repeat identifier values", system_prompt)
        self.assertIn("canonical product or feature display name", system_prompt)
        self.assertIn("Media Relay", result.content)

    def test_render_uses_facts_and_pinned_persona(self) -> None:
        facts = build_automation_reply_facts(
            behavior="detailed_invoice",
            reply_intent="request_missing_information",
            missing_information=["Transaction ID"],
            resolution_status="awaiting_customer",
            customer_name="Jack Gold",
        )
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(text="Could you share the Transaction ID?", model_name="persona-model")
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
            )

        self.assertEqual(result.content, f"Hi Jack,\n\n{response.text}\n\nBest,\nSid\nSupport Engineer 2")
        self.assertEqual(result.model, "persona-model")
        self.assertIn('"missing_information"', invoke.call_args.kwargs["user_prompt"])
        self.assertIn("Warm", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("Do not write a greeting or signature", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("Hi Jack,", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("warm, natural sentences", invoke.call_args.kwargs["system_prompt"])
        self.assertIn("Best,\nSid\nSupport Engineer 2", invoke.call_args.kwargs["system_prompt"])

    def test_customer_first_name_uses_first_token_and_safe_fallback(self) -> None:
        self.assertEqual(customer_first_name("  Jack   Gold  "), "Jack")
        self.assertEqual(customer_first_name("陈小明"), "陈小明")
        self.assertEqual(customer_first_name("Mary-Jane Watson"), "Mary-Jane")
        self.assertEqual(customer_first_name("customer@example.com"), "Customer")
        self.assertEqual(customer_first_name("Jack<script>"), "Customer")
        self.assertEqual(customer_first_name(""), "Customer")

    def test_render_removes_model_generated_greeting_before_adding_configured_greeting(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(text="Hi Jack, Thanks for reaching out.", model_name="persona-model")
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
            )

        self.assertEqual(result.content, "Hi Jack,\n\nThanks for reaching out.\n\nBest,\nSid")

    def test_legacy_signoff_name_is_rendered_as_a_signature(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True, model="persona-model")
        response = SimpleNamespace(text="The request is complete.", model_name="persona-model")
        with patch("backend.services.automation_persona.resolve_model_profile", return_value=profile), patch(
            "backend.services.automation_persona.invoke_responses_text", return_value=response
        ):
            result = render_automation_reply(
                reply_facts={"behavior": "quota", "reply_intent": "resolution_update"},
                persona_assignment={"content": {"instruction": "Warm", "signoff_name": "Maya"}},
            )

        self.assertTrue(result.content.endswith("Best Regards,\nMaya"))

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

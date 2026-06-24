from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from backend.services.billing_automation import (
    build_billing_automation_result,
    send_billing_internal_email,
)
from backend.services.support_router_prompt import build_route_prompt_hints
from backend.services.support_router import (
    SupportRouteDecision,
    build_refusal_answer,
    citations_use_authoritative_source,
    decide_support_route,
    resolve_support_message,
    search_agora_public_info,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class SupportRouterTests(unittest.TestCase):
    _BAN_API_MISMATCH_MESSAGE = """Hello, Agora team.

We are using the Ban User Privileges API (POST /dev/v1/kicking-rule) to disband channels after a broadcast ends, but we have found some differences between the official documentation and the actual API behavior.

1. uid: 0 cannot be used
According to the documentation
(https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel), when targeting all users in a channel, it says to use uid: 0. However, in actual use:
"uid": 0 (number) -> Error: uid '0' must be a number, or set str_uid = true

2. Cannot create a permanent rule with time: 0
The documentation states that time: 0 means the rule is applied permanently. However, when we actually send time: 0, the API returns {"status":"success","id":0}, but no rule is created."""
    _TK_165_MESSAGE = (
        "Hi, We are implementing agora broadcasting and currently need some more info on products that "
        "agora provides. Would be great if we can connect with someone who can guide us on products "
        'that Agora has and could help us."'
    )

    def test_build_route_prompt_hints_captures_product_mode_and_context_signals(self) -> None:
        hints = build_route_prompt_hints(
            "What's the real difference between COMMUNICATION and LIVE_BROADCASTING?",
            ticket_subject="Agora profile choice",
            ticket_context=[
                {"role": "customer", "content": "I need better viewer analytics."},
                {"role": "assistant", "content": "Let's compare the profiles."},
            ],
        )

        self.assertIn("communication", hints["message_matches"]["technical"])
        self.assertIn("live broadcasting", hints["message_matches"]["technical"])
        self.assertIn("viewer analytics", hints["context_matches"]["technical"])
        self.assertIn("agora", hints["context_matches"]["agora"])
        self.assertTrue(hints["flags"]["looks_like_question"])

    def test_build_route_prompt_hints_marks_docs_eval_anchor_terms(self) -> None:
        hints = build_route_prompt_hints(
            "Why are parameter mismatch questions good for testing a docs-based RAG?",
            ticket_subject="Auth benchmark quality",
            ticket_context=[
                {"role": "customer", "content": "I want the auth test set to catch token construction mistakes."},
            ],
        )

        self.assertIn("parameter mismatch", hints["message_matches"]["technical"])
        self.assertIn("docs-based rag", hints["message_matches"]["technical"])
        self.assertIn("auth benchmark", hints["context_matches"]["technical"])
        self.assertTrue(hints["flags"]["looks_like_question"])

    def test_build_route_prompt_hints_treats_black_screen_as_technical_symptom(self) -> None:
        hints = build_route_prompt_hints("I got a black screen issue after joining the call, what should I do?")

        self.assertIn("black screen", hints["message_matches"]["technical"])
        self.assertNotIn("black screen", hints["message_matches"]["system"])
        self.assertTrue(hints["flags"]["looks_like_question"])

    def test_build_route_prompt_hints_marks_agora_product_portfolio_signals(self) -> None:
        hints = build_route_prompt_hints(self._TK_165_MESSAGE)

        self.assertIn("products that agora provides", hints["message_matches"]["product_portfolio"])
        self.assertIn("guide us on products", hints["message_matches"]["product_portfolio"])
        self.assertIn("broadcasting", hints["message_matches"]["product_portfolio"])
        self.assertTrue(hints["flags"]["product_portfolio_pattern"])

    def test_decide_support_route_uses_llm_classification_for_small_talk(self) -> None:
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "small_talk",
                    "confidence": 0.92,
                    "reason": "few_shot_small_talk",
                    "matched_signals": ["weather"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route("今天天气怎么样")

        self.assertEqual(decision.scope_label, "small_talk")
        self.assertEqual(decision.route_family, "general_chat")
        self.assertEqual(decision.execution_action, "refuse")
        self.assertEqual(decision.tooling_profile, "no_agora_docs_refusal")
        self.assertEqual(decision.route, "refuse")
        self.assertEqual(decision.reason, "few_shot_small_talk")
        self.assertEqual(decision.matched_signals, ["weather"])

    def test_decide_support_route_routes_agora_technical_question_to_rag(self) -> None:
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "agora_technical",
                    "confidence": 0.94,
                    "reason": "few_shot_product_fit",
                    "matched_signals": ["live broadcasting", "comparison"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route("What's the real difference between COMMUNICATION and LIVE_BROADCASTING?")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.tooling_profile, "agora_docs_only")
        self.assertEqual(decision.route, "rag")
        self.assertEqual(decision.reason, "few_shot_product_fit")

    def test_decide_support_route_fast_paths_channel_join_question_without_llm(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route("how to join channel")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.reason, "channel_joining_support")
        self.assertEqual(decision.matched_signals, ["join channel", "channel", "looks_like_question"])
        urlopen_mock.assert_not_called()

    def test_decide_support_route_fast_paths_black_screen_troubleshooting_without_llm(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route("I got black screen, what should I do?")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.reason, "technical_troubleshooting_symptom")
        self.assertIn("black screen", decision.matched_signals)
        self.assertIn("looks_like_question", decision.matched_signals)
        urlopen_mock.assert_not_called()

    def test_decide_support_route_black_screen_troubleshooting_ignores_misleading_subject_without_llm(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route(
                "I got black screen, what should I do?",
                ticket_subject="Black Screen After Startup",
            )

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.reason, "technical_troubleshooting_symptom")
        self.assertIn("black screen", decision.matched_signals)
        urlopen_mock.assert_not_called()

    def test_decide_support_route_fast_paths_general_system_help_without_llm(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route("My computer blue-screened. What should I do?")

        self.assertEqual(decision.scope_label, "non_agora")
        self.assertEqual(decision.route_family, "fallback_or_refuse")
        self.assertEqual(decision.execution_action, "refuse")
        self.assertEqual(decision.reason, "general_it_support")
        self.assertIn("blue screen", decision.matched_signals)
        self.assertIn("looks_like_question", decision.matched_signals)
        urlopen_mock.assert_not_called()

    def test_decide_support_route_fast_paths_docs_api_semantics_without_llm(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route(self._BAN_API_MISMATCH_MESSAGE)

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.reason, "docs_api_semantics_support")
        self.assertIn("docs_url", decision.matched_signals)
        self.assertIn("endpoint_path", decision.matched_signals)
        urlopen_mock.assert_not_called()

    def test_decide_support_route_fast_paths_agora_product_portfolio_without_llm(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route(self._TK_165_MESSAGE)

        self.assertEqual(decision.scope_label, "agora_non_technical")
        self.assertEqual(decision.route_family, "web_company_info")
        self.assertEqual(decision.execution_action, "web_search")
        self.assertEqual(decision.tooling_profile, "official_web_search")
        self.assertEqual(decision.route, "web_search")
        self.assertEqual(decision.reason, "agora_product_portfolio")
        self.assertIn("products that agora provides", decision.matched_signals)
        self.assertIn("guide us on products", decision.matched_signals)
        self.assertIn("broadcasting", decision.matched_signals)
        urlopen_mock.assert_not_called()

    def test_decide_support_route_fast_paths_agora_product_portfolio_variants_without_llm(self) -> None:
        variants = (
            "What products does Agora provide for broadcasting?",
            "Which Agora product should we use for broadcasting?",
            "Could you guide us on Agora products for broadcasting?",
        )

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            for message in variants:
                with self.subTest(message=message):
                    decision = decide_support_route(message)
                    self.assertEqual(decision.scope_label, "agora_non_technical")
                    self.assertEqual(decision.execution_action, "web_search")
                    self.assertEqual(decision.reason, "agora_product_portfolio")

        urlopen_mock.assert_not_called()

    def test_decide_support_route_fast_paths_account_suspension_without_llm(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route("Our account was suspended and we need help getting it reviewed.")

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.route_family, "billing_automation")
        self.assertEqual(decision.execution_action, "account_suspension")
        self.assertEqual(decision.tooling_profile, "deterministic_billing_intake")
        self.assertEqual(decision.route, "account_suspension")
        self.assertEqual(decision.reason, "billing_account_suspension")
        self.assertIn("account suspended", decision.matched_signals)
        urlopen_mock.assert_not_called()

    def test_decide_support_route_fast_paths_detailed_invoice_without_llm(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route(
                "Please send a detailed invoice for Transaction ID 1104245232004173824, "
                "Issue date 6 May 2026, Amount USD 705.97."
            )

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.route_family, "billing_automation")
        self.assertEqual(decision.execution_action, "detailed_invoice")
        self.assertEqual(decision.tooling_profile, "deterministic_billing_intake")
        self.assertEqual(decision.route, "detailed_invoice")
        self.assertEqual(decision.reason, "billing_detailed_invoice")
        self.assertIn("detailed invoice", decision.matched_signals)
        urlopen_mock.assert_not_called()

    def test_decide_support_route_accepts_llm_billing_scope_only_with_execution_action(self) -> None:
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "execution_action": "detailed_invoice",
                    "confidence": 0.91,
                    "reason": "billing_detailed_invoice",
                    "matched_signals": ["invoice details"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route("I need a billing document for my last payment.")

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.route_family, "billing_automation")
        self.assertEqual(decision.execution_action, "detailed_invoice")
        self.assertEqual(decision.tooling_profile, "deterministic_billing_intake")

    def test_resolve_support_message_collects_missing_account_suspension_fields(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="account_suspension",
            confidence=0.98,
            reason="billing_account_suspension",
            matched_signals=["account suspended"],
            response_language="en",
        )

        resolution = resolve_support_message(
            "Our account was suspended. Company name: ExampleCo. Website: https://example.com",
            decision=decision,
        )

        self.assertEqual(resolution.answer_route, "workflow")
        self.assertEqual(resolution.route_family, "billing_automation")
        self.assertEqual(resolution.execution_action, "account_suspension")
        self.assertEqual(resolution.tooling_profile, "deterministic_billing_intake")
        self.assertFalse(resolution.needs_engineer_guidance)
        self.assertIn("Company location:", resolution.answer)
        self.assertIn("Contact email:", resolution.answer)
        self.assertIn("Phone number:", resolution.answer)
        self.assertIn("Use Case:", resolution.answer)
        self.assertNotIn("Company name:", resolution.answer)

    def test_resolve_support_message_builds_account_suspension_internal_email_when_ready(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="account_suspension",
            confidence=0.98,
            reason="billing_account_suspension",
            matched_signals=["account suspended"],
            response_language="en",
        )

        resolution = resolve_support_message(
            (
                "Our account was suspended. Company name: ExampleCo. Company location: San Francisco, CA. "
                "Website: https://example.com. Contact email: ops@example.com. Phone number: +1 415 555 0100. "
                "Use Case: We provide live shopping video support."
            ),
            decision=decision,
        )

        self.assertEqual(resolution.answer_route, "workflow")
        self.assertEqual(resolution.execution_action, "account_suspension")
        self.assertIn("We’ve escalated your account suspension request", resolution.answer)
        self.assertIsNotNone(resolution.evidence_summary)
        assert resolution.evidence_summary is not None
        internal_email = resolution.evidence_summary["billing_internal_email"]
        self.assertEqual(internal_email["subject"], "Account suspension review request - Ticket {{ticket_id}}")
        self.assertIn("Company name: ExampleCo", internal_email["body"])
        self.assertIn("Contact email: ops@example.com", internal_email["body"])

    def test_resolve_support_message_builds_detailed_invoice_internal_email_when_ready(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="detailed_invoice",
            confidence=0.98,
            reason="billing_detailed_invoice",
            matched_signals=["detailed invoice"],
            response_language="en",
        )

        resolution = resolve_support_message(
            "Please send the detailed invoice. Issue date: 6 May 2026. "
            "Transaction ID: 1104245232004173824. Amount: USD 705.97.",
            decision=decision,
        )

        self.assertEqual(resolution.answer_route, "workflow")
        self.assertEqual(resolution.execution_action, "detailed_invoice")
        self.assertIn("We’ve escalated your detailed invoice request", resolution.answer)
        self.assertIsNotNone(resolution.evidence_summary)
        assert resolution.evidence_summary is not None
        internal_email = resolution.evidence_summary["billing_internal_email"]
        self.assertEqual(internal_email["subject"], "Detailed invoice request - Ticket {{ticket_id}}")
        self.assertIn("Issue date: 6 May 2026", internal_email["body"])
        self.assertIn("Transaction ID: 1104245232004173824", internal_email["body"])
        self.assertIn("Amount: USD 705.97", internal_email["body"])

    def test_resolve_support_message_records_billing_email_send_status_when_ready(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="detailed_invoice",
            confidence=0.98,
            reason="billing_detailed_invoice",
            matched_signals=["detailed invoice"],
            response_language="en",
        )

        with patch(
            "backend.services.support_router.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_mock:
            resolution = resolve_support_message(
                "Please send the detailed invoice. Issue date: 6 May 2026. "
                "Transaction ID: 1104245232004173824. Amount: USD 705.97.",
                ticket_id="TK-BILL-1",
                customer_id="customer@example.com",
                decision=decision,
            )

        self.assertIsNotNone(resolution.evidence_summary)
        assert resolution.evidence_summary is not None
        self.assertEqual(resolution.evidence_summary["billing_internal_email_send_status"], "sent")
        self.assertEqual(resolution.evidence_summary["billing_internal_email_send_reason"], "")
        send_mock.assert_called_once()
        payload = send_mock.call_args.args[0]
        self.assertEqual(payload["to"], "xieziling@agora.io")
        self.assertEqual(payload["from"], "xieziling97@163.com")
        self.assertEqual(payload["subject"], "Detailed invoice request - Ticket TK-BILL-1")

    def test_billing_internal_email_uses_default_recipient_and_sender(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = build_billing_automation_result(
                action="detailed_invoice",
                message=(
                    "Please send the detailed invoice. Issue date: 6 May 2026. "
                    "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                ),
                ticket_id="TK-BILL-1",
                customer_email="customer@example.com",
            )

        self.assertIsNotNone(result.internal_email)
        assert result.internal_email is not None
        self.assertEqual(result.internal_email["to"], "xieziling@agora.io")
        self.assertEqual(result.internal_email["from"], "xieziling97@163.com")

    def test_billing_internal_email_uses_action_specific_destinations(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL": "suspension@example.com",
                "BILLING_AUTOMATION_DETAILED_INVOICE_EMAIL": "invoice@example.com",
            },
            clear=True,
        ):
            suspension = build_billing_automation_result(
                action="account_suspension",
                message=(
                    "Our account was suspended. Company name: ExampleCo. Company location: San Francisco, CA. "
                    "Website: https://example.com. Contact email: ops@example.com. Phone number: +1 415 555 0100. "
                    "Use Case: We provide live shopping video support."
                ),
                ticket_id="TK-SUSP-1",
                customer_email="customer@example.com",
            )
            invoice = build_billing_automation_result(
                action="detailed_invoice",
                message=(
                    "Please send the detailed invoice. Issue date: 6 May 2026. "
                    "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                ),
                ticket_id="TK-BILL-1",
                customer_email="customer@example.com",
            )

        assert suspension.internal_email is not None
        assert invoice.internal_email is not None
        self.assertEqual(suspension.internal_email["to"], "suspension@example.com")
        self.assertEqual(invoice.internal_email["to"], "invoice@example.com")

    def test_billing_internal_email_action_destination_falls_back_to_generic_destination(self) -> None:
        with patch.dict(
            os.environ,
            {"BILLING_AUTOMATION_INTERNAL_EMAIL": "billing@example.com"},
            clear=True,
        ):
            result = build_billing_automation_result(
                action="detailed_invoice",
                message=(
                    "Please send the detailed invoice. Issue date: 6 May 2026. "
                    "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                ),
                ticket_id="TK-BILL-1",
                customer_email="customer@example.com",
            )

        assert result.internal_email is not None
        self.assertEqual(result.internal_email["to"], "billing@example.com")

    def test_account_verification_extracts_ticket_c31612_bullet_sections_cleanly(self) -> None:
        message = """Dear Agora Support,

Our account (user@example.com) was suspended on May 28, 2026 for "Suspicious Activity".

[Company Information]
- Company: Wai-up (와이업)
- Country: Republic of Korea
- Address: 218, Hyangdong-ro, Deogyang-gu, Goyang-si, Gyeonggi-do, Republic of Korea
- Service URL: https://factory-chat-youyeon1.vercel.app (https://factory-chat-youyeon1.vercel.app/)

[Contact Information]
- Name: Kim Donghan
- Email: user@example.com
- Phone: +82 10 4227 3302

[Use Case]
We are building an internal messenger and video meeting tool for our small Korea-China team.

[Payment]
We are fully willing to register a valid credit card and top-up the wallet immediately upon reactivation.

Our App ID is 7994d63a6ee94bd8b16a65ea0707faad.
"""

        result = build_billing_automation_result(
            action="account_verification",
            message=message,
            ticket_id="TK-ACC-C31612",
            customer_email="user@example.com",
        )

        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.collected_fields["company_name"], "Wai-up (와이업)")
        self.assertEqual(
            result.collected_fields["company_location"],
            "Republic of Korea; 218, Hyangdong-ro, Deogyang-gu, Goyang-si, Gyeonggi-do, Republic of Korea",
        )
        self.assertEqual(
            result.collected_fields["website"],
            "https://factory-chat-youyeon1.vercel.app (https://factory-chat-youyeon1.vercel.app/)",
        )
        self.assertEqual(result.collected_fields["contact_email"], "user@example.com")
        self.assertEqual(result.collected_fields["phone_number"], "+82 10 4227 3302")
        self.assertEqual(
            result.collected_fields["use_case"],
            "We are building an internal messenger and video meeting tool for our small Korea-China team.",
        )
        self.assertEqual(result.collected_fields["app_id"], "7994d63a6ee94bd8b16a65ea0707faad")
        self.assertIsNotNone(result.internal_email)

    def test_account_verification_missing_use_case_asks_for_single_field_inline(self) -> None:
        result = build_billing_automation_result(
            action="account_verification",
            message=(
                "Company name: ExampleCorp. Company location: Singapore. "
                "Website: https://example.com. Contact email: admin@example.com. "
                "Phone number: +65-1234-5678."
            ),
            ticket_id="TK-ACC-6856BF",
            customer_email="customer@example.com",
            requester="Taylor",
        )

        self.assertEqual(result.missing_fields, ["use_case"])
        self.assertTrue(result.customer_reply.startswith("Hi Taylor,"))
        self.assertIn("could you please provide your use case?", result.customer_reply)
        self.assertIn(
            "We would need this information to escalate the request to our internal team.",
            result.customer_reply,
        )
        self.assertNotIn("- Use Case:", result.customer_reply)
        self.assertTrue(result.customer_reply.endswith("Thanks in advance!\nSid"))

    def test_account_verification_two_missing_fields_asks_inline_with_and(self) -> None:
        result = build_billing_automation_result(
            action="account_verification",
            message=(
                "Company name: ExampleCorp. Company location: Singapore. "
                "Website: https://example.com. Contact email: admin@example.com."
            ),
            ticket_id="TK-ACC-6856BF",
            customer_email="customer@example.com",
            requester="Taylor",
        )

        self.assertEqual(result.missing_fields, ["phone_number", "use_case"])
        self.assertIn("could you please provide your use case and phone number?", result.customer_reply)
        self.assertNotIn("- Phone number:", result.customer_reply)
        self.assertTrue(result.customer_reply.endswith("Thanks in advance!\nSid"))

    def test_account_verification_three_missing_fields_uses_detail_list(self) -> None:
        result = build_billing_automation_result(
            action="account_verification",
            message=(
                "Company name: ExampleCorp. Website: https://example.com. "
                "Contact email: admin@example.com."
            ),
            ticket_id="TK-ACC-6856BF",
            customer_email="customer@example.com",
            requester="Taylor",
        )

        self.assertEqual(result.missing_fields, ["company_location", "phone_number", "use_case"])
        self.assertIn("could you please provide the following details?", result.customer_reply)
        self.assertLess(result.customer_reply.index("- Use Case:"), result.customer_reply.index("- Address:"))
        self.assertLess(result.customer_reply.index("- Address:"), result.customer_reply.index("- Phone number:"))
        self.assertIn("- Address:", result.customer_reply)
        self.assertIn("- Phone number:", result.customer_reply)
        self.assertIn("- Use Case:", result.customer_reply)
        self.assertTrue(result.customer_reply.endswith("Thanks in advance!\nSid"))

    def test_account_verification_humanized_reply_rejects_missing_required_fields(self) -> None:
        with patch("backend.services.billing_automation.resolve_model_profile") as profile_mock, patch(
            "backend.services.billing_automation.invoke_responses_text"
        ) as invoke_mock:
            profile_mock.return_value.has_invocation_credentials.return_value = True
            invoke_mock.return_value.text = (
                "Hi Taylor,\n\nCould you please provide your use case? We would need this information to "
                "escalate the request to our internal team.\n\nThanks in advance!\nSid"
            )

            result = build_billing_automation_result(
                action="account_verification",
                message=(
                    "Company name: ExampleCorp. Website: https://example.com. "
                    "Contact email: admin@example.com."
                ),
                ticket_id="TK-ACC-6856BF",
                customer_email="customer@example.com",
                requester="Taylor",
            )

        self.assertIn("- Address:", result.customer_reply)
        self.assertIn("- Phone number:", result.customer_reply)
        self.assertIn("- Use Case:", result.customer_reply)

    def test_send_billing_internal_email_skips_when_smtp_password_missing(self) -> None:
        email_payload = {
            "to": "xieziling@agora.io",
            "from": "xieziling97@163.com",
            "subject": "Detailed invoice request - Ticket TK-BILL-1",
            "body": "Hi team",
        }

        with patch.dict(os.environ, {}, clear=True), patch("smtplib.SMTP_SSL") as smtp_mock:
            result = send_billing_internal_email(email_payload)

        self.assertEqual(result["status"], "skipped_config_missing")
        self.assertIn("BILLING_AUTOMATION_SMTP_PASSWORD", result["reason"])
        smtp_mock.assert_not_called()

    def test_send_billing_internal_email_sends_via_smtp_when_configured(self) -> None:
        email_payload = {
            "to": "xieziling@agora.io",
            "from": "xieziling97@163.com",
            "subject": "Detailed invoice request - Ticket TK-BILL-1",
            "body": "Hi team",
        }

        with patch.dict(
            os.environ,
            {
                "BILLING_AUTOMATION_SMTP_PASSWORD": "app-password",
                "BILLING_AUTOMATION_SMTP_HOST": "smtp.163.com",
                "BILLING_AUTOMATION_SMTP_PORT": "465",
            },
            clear=True,
        ), patch("smtplib.SMTP_SSL") as smtp_mock:
            result = send_billing_internal_email(email_payload)

        self.assertEqual(result["status"], "sent")
        smtp = smtp_mock.return_value.__enter__.return_value
        smtp.login.assert_called_once_with("xieziling97@163.com", "app-password")
        sent_message = smtp.send_message.call_args.args[0]
        self.assertEqual(sent_message["To"], "xieziling@agora.io")
        self.assertEqual(sent_message["From"], "xieziling97@163.com")
        self.assertEqual(sent_message["Subject"], "Detailed invoice request - Ticket TK-BILL-1")
        self.assertEqual(sent_message.get_content().strip(), "Hi team")

    def test_resolve_support_message_excludes_detailed_invoice_amount_disputes(self) -> None:
        decision = decide_support_route("The invoice amount is wrong and I want a refund.")

        self.assertNotEqual(decision.execution_action, "detailed_invoice")

    def test_decide_support_route_uses_llm_classification_for_public_info(self) -> None:
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "agora_non_technical",
                    "confidence": 0.91,
                    "reason": "few_shot_company_info",
                    "matched_signals": ["ceo", "agora"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route("who's the ceo of agora")

        self.assertEqual(decision.scope_label, "agora_non_technical")
        self.assertEqual(decision.route_family, "web_company_info")
        self.assertEqual(decision.execution_action, "web_search")
        self.assertEqual(decision.tooling_profile, "official_web_search")
        self.assertEqual(decision.route, "web_search")

    def test_decide_support_route_uses_context_in_prompt_hints(self) -> None:
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "agora_technical",
                    "confidence": 0.89,
                    "reason": "few_shot_follow_up",
                    "matched_signals": ["token", "it still"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(
                "it still doesn't work",
                ticket_subject="Agora RTC token issue",
                ticket_context=[
                    {"role": "customer", "content": "My Agora SDK token is invalid."},
                    {"role": "assistant", "content": "Please check the token builder configuration."},
                ],
            )

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.reason, "few_shot_follow_up")

    def test_decide_support_route_routes_ticket_resolution_after_substantive_answer(self) -> None:
        decision = decide_support_route(
            "got it, thanks",
            ticket_subject="Join channel",
            ticket_context=[
                {"role": "customer", "content": "how to join channel"},
                {
                    "role": "assistant",
                    "content": "Use joinChannel with the same channel name and token.",
                },
            ],
            latest_assistant_message={
                "role": "assistant",
                "content": "Use joinChannel with the same channel name and token.",
                "workflow_action": "answer_customer",
                "answer_route": "rag",
                "route_reason": "grounded_answer",
                "execution_action": "rag",
            },
            current_ticket_status="communicating",
        )

        self.assertEqual(decision.scope_label, "ticket_resolution")
        self.assertEqual(decision.route_family, "ticket_resolution")
        self.assertEqual(decision.execution_action, "resolve_ticket")
        self.assertEqual(decision.route, "resolve_ticket")
        self.assertEqual(decision.reason, "customer_confirmed_resolved")
        self.assertIn("got it", decision.matched_signals)
        self.assertIn("thanks", decision.matched_signals)

    def test_decide_support_route_routes_ticket_resolution_after_engineer_guidance(self) -> None:
        decision = decide_support_route(
            "it worked, thanks!",
            ticket_subject="Black screen issue",
            ticket_context=[
                {"role": "customer", "content": "black screen issue"},
                {
                    "role": "assistant",
                    "content": "Please try switching to another camera and test again.",
                },
            ],
            latest_assistant_message={
                "role": "assistant",
                "content": "Please try switching to another camera and test again.",
                "assistant_message_source": "engineer_guidance",
                "supports_customer_resolution": True,
            },
            current_ticket_status="communicating",
        )

        self.assertEqual(decision.scope_label, "ticket_resolution")
        self.assertEqual(decision.route_family, "ticket_resolution")
        self.assertEqual(decision.execution_action, "resolve_ticket")
        self.assertEqual(decision.reason, "customer_confirmed_resolved")
        self.assertIn("it worked", decision.matched_signals)
        self.assertIn("thanks", decision.matched_signals)

    def test_decide_support_route_returns_controlled_response_for_gratitude_after_non_substantive_reply(self) -> None:
        decision = decide_support_route(
            "thanks",
            ticket_subject="Join channel",
            ticket_context=[
                {"role": "customer", "content": "how to join channel"},
                {
                    "role": "assistant",
                    "content": "What error or blocker are you seeing?",
                },
            ],
            latest_assistant_message={
                "role": "assistant",
                "content": "What error or blocker are you seeing?",
                "workflow_action": "clarify_customer_for_intake",
                "answer_route": "rag",
                "route_reason": "rag_insufficient_evidence",
                "execution_action": "rag",
            },
            current_ticket_status="communicating",
        )

        self.assertEqual(decision.scope_label, "small_talk")
        self.assertEqual(decision.route_family, "general_chat")
        self.assertEqual(decision.execution_action, "controlled_response")
        self.assertEqual(decision.route, "controlled_response")
        self.assertEqual(decision.reason, "gratitude_acknowledgement")
        self.assertIn("thanks", decision.matched_signals)

    def test_resolve_support_message_returns_neutral_gratitude_acknowledgement_when_not_resolving(self) -> None:
        resolution = resolve_support_message(
            "thanks",
            ticket_subject="Join channel",
            ticket_context=[
                {"role": "customer", "content": "how to join channel"},
                {
                    "role": "assistant",
                    "content": "What error or blocker are you seeing?",
                },
            ],
            latest_assistant_message={
                "role": "assistant",
                "content": "What error or blocker are you seeing?",
                "workflow_action": "clarify_customer_for_intake",
                "answer_route": "rag",
                "route_reason": "rag_insufficient_evidence",
                "execution_action": "rag",
            },
            current_ticket_status="communicating",
        )

        self.assertEqual(resolution.answer_route, "controlled_response")
        self.assertEqual(resolution.execution_action, "controlled_response")
        self.assertEqual(resolution.route_reason, "gratitude_acknowledgement")
        self.assertEqual(
            resolution.answer,
            "You're welcome. If you need anything else for this ticket, send the next detail here and I'll continue helping.",
        )

    def test_decide_support_route_includes_selected_product_in_llm_prompt(self) -> None:
        captured_request: dict[str, object] = {}
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "agora_technical",
                    "confidence": 0.9,
                    "reason": "product_scoped_route",
                    "matched_signals": ["cloud recording"],
                }
            )
        }

        def _capture(request, timeout=None):
            captured_request["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(payload)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            side_effect=_capture,
        ):
            decision = decide_support_route(
                "How do I start recording?",
                product="cloud_recording",
            )

        serialized_input = json.dumps(captured_request["body"]["input"], ensure_ascii=False)
        self.assertIn("Cloud Recording", serialized_input)
        self.assertEqual(decision.reason, "product_scoped_route")

    def test_decide_support_route_falls_back_to_agora_technical_for_ambiguous_messages(self) -> None:
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "agora_technical",
                    "confidence": 0.4,
                    "reason": "uncertain_route",
                    "matched_signals": ["question"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route("what should I do next")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.route, "rag")
        self.assertEqual(decision.reason, "conservative_agora_technical_fallback")
        self.assertEqual(decision.router_source, "conservative_fallback")
        self.assertTrue(decision.intent_router_attempted)
        self.assertEqual(decision.intent_router_fallback_reason, "below_confidence_threshold")
        self.assertEqual(decision.intent_router_model_confidence, 0.4)
        self.assertIsNotNone(decision.intent_router_confidence_threshold)
        self.assertGreater(decision.intent_router_confidence_threshold, 0.0)

    def test_decide_support_route_falls_back_to_agora_technical_on_invalid_json(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse({"output_text": "not-json"}),
        ):
            decision = decide_support_route("Would Notifications help me build viewer analytics?")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.reason, "conservative_agora_technical_fallback")
        self.assertEqual(decision.router_source, "conservative_fallback")
        self.assertTrue(decision.intent_router_attempted)
        self.assertEqual(decision.intent_router_fallback_reason, "invalid_json")
        self.assertEqual(decision.intent_router_failure_type, "invalid_json")
        self.assertEqual(decision.intent_router_failure_source, "responses_api")

    def test_decide_support_route_falls_back_to_agora_technical_on_http_failure(self) -> None:
        def _raise_http_error(*_args, **_kwargs):
            raise urllib.error.HTTPError(
                url="https://api.openai.com/v1/responses",
                code=500,
                msg="Internal Server Error",
                hdrs=None,
                fp=io.BytesIO(b'{"error":{"message":"boom"}}'),
            )

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            side_effect=_raise_http_error,
        ):
            decision = decide_support_route("If compliance requires one file per participant, should I avoid composite?")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.reason, "conservative_agora_technical_fallback")
        self.assertEqual(decision.router_source, "conservative_fallback")
        self.assertTrue(decision.intent_router_attempted)
        self.assertEqual(decision.intent_router_fallback_reason, "llm_invocation_failed")
        self.assertEqual(decision.intent_router_failure_type, "llm_invocation_failed")
        self.assertEqual(decision.intent_router_failure_source, "responses_api")

    def test_decide_support_route_uses_conservative_fallback_when_router_unavailable(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=True):
            decision = decide_support_route("What is the real difference between COMMUNICATION and LIVE_BROADCASTING?")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.reason, "conservative_agora_technical_fallback")
        self.assertEqual(decision.router_source, "conservative_fallback")
        self.assertTrue(decision.intent_router_attempted)
        self.assertEqual(decision.intent_router_fallback_reason, "missing_credentials")
        self.assertEqual(decision.intent_router_failure_type, "missing_credentials")
        self.assertEqual(decision.intent_router_failure_source, "profile_check")

    def test_llm_route_decision_uses_responses_payload_with_configured_settings(self) -> None:
        captured_request: dict[str, object] = {}
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "agora_technical",
                    "confidence": 0.94,
                    "reason": "few_shot_route",
                    "matched_signals": ["live broadcasting", "comparison"],
                }
            )
        }

        def _capture(request, timeout=None):
            captured_request["url"] = request.full_url
            captured_request["body"] = json.loads(request.data.decode("utf-8"))
            captured_request["timeout"] = timeout
            return _FakeResponse(payload)

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "INTENT_ROUTER_MODEL": "gpt-5.4-mini",
                "INTENT_ROUTER_REASONING_EFFORT": "low",
                "INTENT_ROUTER_TEMPERATURE": "0.3",
            },
            clear=True,
        ), patch("urllib.request.urlopen", side_effect=_capture):
            decision = decide_support_route("What's the real difference between COMMUNICATION and LIVE_BROADCASTING?")

        request_body = captured_request["body"]
        self.assertEqual(captured_request["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(request_body["model"], "gpt-5.4-mini")
        self.assertEqual(request_body["reasoning"]["effort"], "low")
        self.assertEqual(request_body["temperature"], 0.3)
        self.assertIn("COMMUNICATION", json.dumps(request_body["input"], ensure_ascii=False))
        self.assertIn("parameter mismatch", json.dumps(request_body["input"], ensure_ascii=False))
        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.router_source, "llm_semantic")
        self.assertTrue(decision.intent_router_attempted)
        self.assertEqual(decision.intent_router_model_confidence, 0.94)
        self.assertIsNotNone(decision.intent_router_confidence_threshold)
        self.assertIsNone(decision.intent_router_fallback_reason)
        self.assertIsNone(decision.intent_router_failure_type)

    def test_decide_support_route_retries_without_temperature_when_model_rejects_it(self) -> None:
        calls: list[dict[str, object]] = []
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "agora_technical",
                    "confidence": 0.9,
                    "reason": "temperature_retry",
                    "matched_signals": ["live broadcasting", "comparison", "comparison"],
                }
            )
        }

        def _capture(request, timeout=None):
            calls.append(json.loads(request.data.decode("utf-8")))
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    url="https://api.openai.com/v1/responses",
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(b'{"error":{"message":"Unsupported temperature for this model"}}'),
                )
            return _FakeResponse(payload)

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "INTENT_ROUTER_MODEL": "gpt-5.4-mini",
                "INTENT_ROUTER_REASONING_EFFORT": "low",
                "INTENT_ROUTER_TEMPERATURE": "0.3",
            },
            clear=True,
        ), patch("urllib.request.urlopen", side_effect=_capture):
            decision = decide_support_route("What's the real difference between COMMUNICATION and LIVE_BROADCASTING?")

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["temperature"], 0.3)
        self.assertNotIn("temperature", calls[1])
        self.assertEqual(calls[1]["model"], "gpt-5.4-mini")
        self.assertEqual(calls[1]["reasoning"]["effort"], "low")
        self.assertEqual(decision.reason, "temperature_retry")
        self.assertEqual(decision.matched_signals, ["live broadcasting", "comparison"])

    def test_build_refusal_answer_uses_chinese_template(self) -> None:
        answer = build_refusal_answer(
            SupportRouteDecision(
                scope_label="small_talk",
                route="refuse",
                confidence=0.98,
                reason="small_talk_detected",
                matched_signals=["weather"],
                response_language="zh",
            )
        )

        self.assertEqual(
            answer,
            "我是 Agora 的 Support AI，主要回答 Agora 相关问题。这个问题不在我的支持范围内。如果你有 Agora 产品、SDK、API 或集成相关问题，我可以继续帮你。",
        )

    def test_resolve_support_message_returns_refusal_for_general_chat(self) -> None:
        decision = SupportRouteDecision(
            scope_label="small_talk",
            route="refuse",
            confidence=0.92,
            reason="few_shot_small_talk",
            matched_signals=["hello"],
            response_language="en",
        )

        resolution = resolve_support_message("hello there", decision=decision)

        self.assertEqual(resolution.route_family, "general_chat")
        self.assertEqual(resolution.execution_action, "refuse")
        self.assertEqual(resolution.tooling_profile, "no_agora_docs_refusal")
        self.assertEqual(resolution.answer_route, "refuse")
        self.assertEqual(
            resolution.answer,
            "I'm Agora's support AI and mainly answer Agora-related questions. This request is outside my support scope. If you have an Agora product, SDK, API, or integration question, I can help with that.",
        )


class AgoraPublicInfoSearchTests(unittest.TestCase):
    def test_search_agora_public_info_uses_safer_default_timeout_budget(self) -> None:
        captured_request: dict[str, object] = {}
        payload = {
            "output_text": "Tony Zhao is Agora's CEO.",
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "url": "https://investor.agora.io/corporate/senior-leadership/",
                                "title": "Senior Leadership",
                            }
                        ]
                    },
                }
            ],
        }

        def _capture(request, timeout=None):
            captured_request["timeout"] = timeout
            return _FakeResponse(payload)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            side_effect=_capture,
        ):
            answer = search_agora_public_info("who's the ceo of agora", response_language="en")

        self.assertEqual(captured_request["timeout"], 30.0)
        self.assertTrue(answer.search_used)

    def test_citations_use_authoritative_source_accepts_official_and_market_domains(self) -> None:
        self.assertTrue(
            citations_use_authoritative_source(
                [{"source_url": "https://investor.agora.io/corporate/senior-leadership/"}]
            )
        )
        self.assertTrue(
            citations_use_authoritative_source(
                [{"source_url": "https://www.sec.gov/Archives/edgar/data/0000000000/example.htm"}]
            )
        )
        self.assertFalse(
            citations_use_authoritative_source(
                [{"source_url": "https://example.com/blog/agora"}]
            )
        )

    def test_search_agora_public_info_parses_citations_and_sources(self) -> None:
        payload = {
            "output_text": "Agora's CEO is Tony Zhao.[1]",
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "url": "https://www.agora.io/en/about-agora/",
                                "title": "About Agora",
                            }
                        ]
                    },
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Agora's CEO is Tony Zhao.[1]",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.agora.io/en/about-agora/",
                                    "title": "About Agora",
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(),
        ):
            answer = search_agora_public_info("who's the ceo of agora", response_language="en")

        self.assertIn("Tony Zhao", answer.answer)
        self.assertEqual(answer.citations[0]["source_url"], "https://www.agora.io/en/about-agora/")
        self.assertIn("https://www.agora.io/en/about-agora/", answer.sources)

    def test_search_agora_public_info_moves_markdown_links_out_of_answer_body(self) -> None:
        payload = {
            "output_text": (
                'Tony Zhao (Bin "Tony" Zhao). '
                "([investor.agora.io](https://investor.agora.io/corporate/senior-leadership/?utm_source=openai))"
            ),
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "url": "https://investor.agora.io/corporate/senior-leadership/?utm_source=openai",
                                "title": "Senior Leadership",
                            }
                        ]
                    },
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                'Tony Zhao (Bin "Tony" Zhao). '
                                "([investor.agora.io](https://investor.agora.io/corporate/senior-leadership/?utm_source=openai))"
                            ),
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://investor.agora.io/corporate/senior-leadership/?utm_source=openai",
                                    "title": "Senior Leadership",
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(),
        ):
            answer = search_agora_public_info("who's the ceo of agora", response_language="en")

        self.assertEqual(answer.answer, 'Tony Zhao (Bin "Tony" Zhao).')
        self.assertNotIn("http", answer.answer)
        self.assertNotIn("investor.agora.io", answer.answer)
        self.assertEqual(
            answer.citations[0]["source_url"],
            "https://investor.agora.io/corporate/senior-leadership/?utm_source=openai",
        )
        self.assertIn(
            "https://investor.agora.io/corporate/senior-leadership/?utm_source=openai",
            answer.sources,
        )

    def test_search_agora_public_info_preserves_product_portfolio_bullet_newlines(self) -> None:
        payload = {
            "output_text": (
                "Core products:\n"
                "- **[Broadcast Streaming](https://www.agora.io/en/products/broadcast-streaming/)** — Best for large-scale one-way broadcasting.\n"
                "- **Interactive Live Streaming** — Best for low-latency audience interaction. https://www.agora.io/en/products/interactive-live-streaming/\n\n"
                "Please use Agora's official Talk to Us / Contact Sales path."
            ),
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "url": "https://www.agora.io/en/products/broadcast-streaming/",
                                "title": "Broadcast Streaming",
                            }
                        ]
                    },
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                "Core products:\n"
                                "- **[Broadcast Streaming](https://www.agora.io/en/products/broadcast-streaming/)** — Best for large-scale one-way broadcasting.\n"
                                "- **Interactive Live Streaming** — Best for low-latency audience interaction. https://www.agora.io/en/products/interactive-live-streaming/\n\n"
                                "Please use Agora's official Talk to Us / Contact Sales path."
                            ),
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.agora.io/en/products/broadcast-streaming/",
                                    "title": "Broadcast Streaming",
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            answer = search_agora_public_info(
                SupportRouterTests._TK_165_MESSAGE,
                response_language="en",
                route_reason="agora_product_portfolio",
            )

        self.assertIn("Core products:\n- **Broadcast Streaming**", answer.answer)
        self.assertIn("\n- **Interactive Live Streaming**", answer.answer)
        self.assertNotIn("https://", answer.answer)
        self.assertEqual(
            answer.citations[0]["source_url"],
            "https://www.agora.io/en/products/broadcast-streaming/",
        )

    def test_search_agora_public_info_product_portfolio_uses_official_product_domains_only(self) -> None:
        calls: list[dict[str, object]] = []
        payload = {
            "output_text": "INSUFFICIENT",
            "output": [],
        }

        def _capture(request, timeout=None):
            calls.append(json.loads(request.data.decode("utf-8")))
            return _FakeResponse(payload)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            side_effect=_capture,
        ):
            answer = search_agora_public_info(
                SupportRouterTests._TK_165_MESSAGE,
                response_language="en",
                route_reason="agora_product_portfolio",
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["tools"][0]["filters"]["allowed_domains"],
            ["agora.io", "www.agora.io"],
        )
        self.assertEqual(answer.answer, "INSUFFICIENT")

    def test_resolve_support_message_routes_product_portfolio_via_web_search(self) -> None:
        payload = {
            "output_text": (
                "Broadcast Streaming is designed for large-scale one-way broadcasting, while "
                "Interactive Live Streaming is better for low-latency audience interaction."
            ),
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "url": "https://www.agora.io/en/products/broadcast-streaming/",
                                "title": "Broadcast Streaming",
                            }
                        ]
                    },
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                "Broadcast Streaming is designed for large-scale one-way broadcasting, while "
                                "Interactive Live Streaming is better for low-latency audience interaction."
                            ),
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.agora.io/en/products/broadcast-streaming/",
                                    "title": "Broadcast Streaming",
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        decision = SupportRouteDecision(
            scope_label="agora_non_technical",
            route="web_search",
            confidence=0.97,
            reason="agora_product_portfolio",
            matched_signals=["broadcasting", "products that agora provides"],
            response_language="en",
        )

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            resolution = resolve_support_message(
                SupportRouterTests._TK_165_MESSAGE,
                decision=decision,
            )

        self.assertEqual(resolution.answer_route, "web_search")
        self.assertEqual(resolution.execution_action, "web_search")
        self.assertEqual(resolution.route_reason, "agora_product_portfolio")
        self.assertTrue(resolution.search_used)
        self.assertIn("Broadcast Streaming", resolution.answer)
        self.assertEqual(
            resolution.citations[0]["source_url"],
            "https://www.agora.io/en/products/broadcast-streaming/",
        )

    def test_search_agora_public_info_uses_controlled_fallback_when_openai_is_unavailable(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=True):
            answer = search_agora_public_info("who's the ceo of agora", response_language="en")

        self.assertIn("Agora support agent", answer.answer)
        self.assertEqual(answer.sources, [])
        self.assertEqual(answer.citations, [])

    def test_search_agora_public_info_uses_controlled_fallback_on_request_failure(self) -> None:
        error = urllib.error.HTTPError(
            url="https://api.openai.com/v1/responses",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"boom"}}'),
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            side_effect=error,
        ):
            answer = search_agora_public_info("who's the ceo of agora", response_language="en")

        self.assertIn("Agora support agent", answer.answer)
        self.assertEqual(answer.sources, [])
        self.assertEqual(answer.citations, [])


if __name__ == "__main__":
    unittest.main()

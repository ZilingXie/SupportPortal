from __future__ import annotations

import json
import types
import unittest
from unittest.mock import patch

from backend.services.product_selection import (
    PRODUCT_SELECTION_STATE_AWAITING_CONFIRMATION,
    WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_PRODUCT,
    SupportProductDecision,
    build_product_selection_state,
    decide_support_product,
    detect_explicit_support_product,
    detect_support_product_correction,
    infer_support_product_deterministically,
    resolve_support_product_context,
)
from backend.services.support_products import (
    SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING,
    SUPPORT_PRODUCT_CLOUD_RECORDING,
)
from backend.services.support_router import SupportRouteDecision


def _technical_route() -> SupportRouteDecision:
    return SupportRouteDecision(
        scope_label="agora_technical",
        route="rag",
        confidence=0.94,
        reason="technical_docs_match",
        matched_signals=["technical"],
    )


def _non_technical_route() -> SupportRouteDecision:
    return SupportRouteDecision(
        scope_label="small_talk",
        route="refuse",
        confidence=0.9,
        reason="small_talk",
        matched_signals=["small_talk"],
    )


class ProductSelectionTests(unittest.TestCase):
    def test_detect_explicit_support_product_recognizes_rtc_aliases(self) -> None:
        detected = detect_explicit_support_product("This is an RTC SDK issue in audio/video calling.")
        self.assertEqual(detected, SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING)

    def test_detect_support_product_correction_overrides_existing_product(self) -> None:
        corrected = detect_support_product_correction(
            "Actually this is Cloud Recording, not RTC.",
            SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING,
        )
        self.assertEqual(corrected, SUPPORT_PRODUCT_CLOUD_RECORDING)

    def test_infer_support_product_deterministically_prefers_cloud_recording_signals(self) -> None:
        decision = infer_support_product_deterministically(
            "We call acquire, then start recording, and the sid is returned but no recording file is generated."
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.product, SUPPORT_PRODUCT_CLOUD_RECORDING)
        self.assertEqual(decision.reason, "deterministic_signal_match")
        self.assertIn("sid", decision.matched_signals)

    def test_decide_support_product_uses_llm_fallback_when_signals_are_weak(self) -> None:
        with patch(
            "backend.services.product_selection.resolve_model_profile",
            return_value=types.SimpleNamespace(api_key="test-key"),
        ), patch(
            "backend.services.product_selection.invoke_responses_text",
            return_value=types.SimpleNamespace(
                text=json.dumps(
                    {
                        "product": "audio_video_calling",
                        "confidence": 0.82,
                        "reason": "join_channel_context",
                        "matched_signals": ["join channel", "remote video"],
                    }
                )
            ),
        ):
            decision = decide_support_product(
                message="Users join the call successfully but the remote video does not render on iOS.",
                ticket_subject="video render issue",
                ticket_context=[{"role": "customer", "content": "remote video is blank"}],
            )

        self.assertEqual(decision.product, SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING)
        self.assertEqual(decision.reason, "join_channel_context")
        self.assertEqual(decision.matched_signals, ["join channel", "remote video"])

    def test_resolve_support_product_context_requests_confirmation_for_ambiguous_technical_message(self) -> None:
        context = resolve_support_product_context(
            message="It stopped working after the latest change.",
            ticket_subject="Support request",
            ticket_context=[{"role": "customer", "content": "It stopped working after the latest change."}],
            product=None,
            product_selection_state=None,
            latest_assistant_message=None,
            current_ticket_status="communicating",
            requester="Taylor",
            customer_id="C-001",
            message_created_at="2026-04-16T08:00:00+00:00",
            route_agent=lambda **_kwargs: _technical_route(),
            product_agent=lambda **_kwargs: SupportProductDecision(
                product=None,
                confidence=0.0,
                reason="product_unknown",
                matched_signals=[],
            ),
        )

        self.assertIsNone(context.product)
        self.assertIsNotNone(context.product_selection_state)
        assert context.product_selection_state is not None
        self.assertEqual(
            context.product_selection_state["phase"],
            PRODUCT_SELECTION_STATE_AWAITING_CONFIRMATION,
        )
        self.assertIsNotNone(context.preflight_execution)
        assert context.preflight_execution is not None
        self.assertEqual(
            context.preflight_execution.workflow_action,
            WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_PRODUCT,
        )
        self.assertTrue(context.preflight_execution.answer.startswith("Hi Taylor,"))
        self.assertIn("Audio/Video Calling (RTC)", context.preflight_execution.answer)
        self.assertTrue(context.preflight_execution.answer.endswith("Best Regards,\nSid"))

    def test_resolve_support_product_context_confirms_pending_product_and_combines_original_message(self) -> None:
        state = build_product_selection_state(
            pending_customer_message="I got black screen after joining the call.",
            pending_message_created_at="2026-04-16T08:00:00+00:00",
            now_value="2026-04-16T08:00:05+00:00",
        )

        context = resolve_support_product_context(
            message="rtc",
            ticket_subject="Black screen issue",
            ticket_context=[{"role": "customer", "content": "I got black screen after joining the call."}],
            product=None,
            product_selection_state=state,
            latest_assistant_message=None,
            current_ticket_status="communicating",
            requester="Taylor",
            customer_id="C-001",
            message_created_at="2026-04-16T08:01:00+00:00",
            route_agent=lambda **_kwargs: _technical_route(),
        )

        self.assertEqual(context.product, SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING)
        self.assertIsNone(context.product_selection_state)
        self.assertIn("I got black screen after joining the call.", context.effective_message)
        self.assertIn("Customer follow-up after product confirmation request: rtc", context.effective_message)
        self.assertIsNone(context.preflight_execution)

    def test_resolve_support_product_context_updates_existing_product_on_explicit_correction(self) -> None:
        context = resolve_support_product_context(
            message="Actually this is Cloud Recording, not RTC.",
            ticket_subject="Recorded file missing",
            ticket_context=[],
            product=SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING,
            product_selection_state=None,
            latest_assistant_message=None,
            current_ticket_status="communicating",
            requester="Taylor",
            customer_id="C-001",
            message_created_at="2026-04-16T08:02:00+00:00",
            route_agent=lambda **_kwargs: _technical_route(),
        )

        self.assertEqual(context.product, SUPPORT_PRODUCT_CLOUD_RECORDING)
        self.assertTrue(context.product_changed)
        self.assertIsNone(context.product_selection_state)
        self.assertIsNone(context.preflight_execution)

    def test_resolve_support_product_context_skips_product_confirmation_for_non_technical_route(self) -> None:
        context = resolve_support_product_context(
            message="Thanks for the quick help.",
            ticket_subject="Follow-up",
            ticket_context=[],
            product=None,
            product_selection_state=None,
            latest_assistant_message=None,
            current_ticket_status="communicating",
            requester="Taylor",
            customer_id="C-001",
            message_created_at="2026-04-16T08:03:00+00:00",
            route_agent=lambda **_kwargs: _non_technical_route(),
            product_agent=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("Non-technical messages should not invoke the product agent.")
            ),
        )

        self.assertIsNone(context.product)
        self.assertIsNone(context.product_selection_state)
        self.assertIsNone(context.preflight_execution)
        self.assertEqual(context.route_decision.scope_label, "small_talk")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

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

    def test_decide_support_route_uses_conservative_fallback_when_router_unavailable(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=True):
            decision = decide_support_route("What is the real difference between COMMUNICATION and LIVE_BROADCASTING?")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route_family, "agora_docs_rag")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.reason, "conservative_agora_technical_fallback")

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

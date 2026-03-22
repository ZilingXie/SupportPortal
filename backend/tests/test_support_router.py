from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from backend.services.support_router import (
    SupportRouteDecision,
    build_refusal_answer,
    decide_support_route,
    search_agora_public_info,
)


class SupportRouterTests(unittest.TestCase):
    def test_decide_support_route_refuses_small_talk(self) -> None:
        decision = decide_support_route("今天天气怎么样")

        self.assertEqual(decision.scope_label, "small_talk")
        self.assertEqual(decision.route, "refuse")
        self.assertGreaterEqual(decision.confidence, 0.9)

    def test_decide_support_route_refuses_non_agora_question(self) -> None:
        decision = decide_support_route("我电脑蓝屏了怎么办")

        self.assertEqual(decision.scope_label, "non_agora")
        self.assertEqual(decision.route, "refuse")
        self.assertGreaterEqual(decision.confidence, 0.85)

    def test_decide_support_route_routes_agora_technical_question_to_rag(self) -> None:
        decision = decide_support_route("how to join a channel")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route, "rag")

    def test_decide_support_route_uses_context_for_follow_up(self) -> None:
        decision = decide_support_route(
            "it still doesn't work",
            ticket_subject="Agora RTC token issue",
            ticket_context=[
                {"role": "customer", "content": "My Agora SDK token is invalid."},
                {"role": "assistant", "content": "Please check the token builder configuration."},
            ],
        )

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.route, "rag")

    def test_decide_support_route_routes_agora_non_technical_question_to_web_search(self) -> None:
        decision = decide_support_route("who's the ceo of agora")

        self.assertEqual(decision.scope_label, "agora_non_technical")
        self.assertEqual(decision.route, "web_search")

    def test_decide_support_route_falls_back_to_non_agora_for_ambiguous_messages(self) -> None:
        with patch("backend.services.support_router._llm_route_decision", return_value=None):
            decision = decide_support_route("what should I do next")

        self.assertEqual(decision.scope_label, "non_agora")
        self.assertEqual(decision.route, "refuse")

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

        self.assertIn("我是 Agora 的 support agent", answer)
        self.assertIn("Agora 相关的问题", answer)


class AgoraPublicInfoSearchTests(unittest.TestCase):
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

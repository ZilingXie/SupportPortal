from __future__ import annotations

import unittest

from backend.services.prompts.rag_answer import (
    build_rag_answer_system_prompt,
    build_rag_answer_user_prompt,
)
from backend.services.prompts.rag_sufficiency import (
    build_rag_sufficiency_system_prompt,
    build_rag_sufficiency_user_prompt,
)
from backend.services.prompts.router import (
    build_router_system_prompt,
    build_router_user_prompt,
)
from backend.services.prompts.web_search import (
    build_web_search_system_prompt,
    build_web_search_user_prompt,
)


class PromptModuleTests(unittest.TestCase):
    def test_router_prompt_v2_is_sectioned_and_classification_only(self) -> None:
        system_prompt = build_router_system_prompt(
            route_examples=[
                {
                    "message": "Who's the CEO of Agora?",
                    "hints": {"agora": ["agora"], "public_info": ["ceo"]},
                    "output": {
                        "scope_label": "agora_non_technical",
                        "confidence": 0.98,
                        "reason": "agora_company_info",
                        "matched_signals": ["agora", "ceo"],
                    },
                }
            ]
        )
        user_prompt = build_router_user_prompt(
            payload={
                "message": "I got black screen issue, what should I do?",
                "ticket_subject": "RTC issue",
                "ticket_context": [{"role": "customer", "content": "remote video is black"}],
                "response_language": "en",
                "hints": {"technical": ["black screen"], "flags": ["looks_like_question"]},
            }
        )

        self.assertIn("## Role", system_prompt)
        self.assertIn("You only classify the request. You do not answer it.", system_prompt)
        self.assertIn("## Few-shot Examples", system_prompt)
        self.assertIn("prefer agora_technical", system_prompt)
        self.assertIn("## Inputs", user_prompt)
        self.assertIn("## Latest Message", user_prompt)
        self.assertIn("## Routing Hints", user_prompt)
        self.assertIn("black screen", user_prompt)

    def test_web_search_prompt_v2_is_grounded_and_has_insufficient_fallback(self) -> None:
        system_prompt = build_web_search_system_prompt(
            response_language="en",
            official_only=True,
        )
        user_prompt = build_web_search_user_prompt(question="Who's the CEO of Agora?")

        self.assertIn("## Role", system_prompt)
        self.assertIn("only from retrieved sources", system_prompt)
        self.assertIn("reply exactly INSUFFICIENT", system_prompt)
        self.assertIn("## Few-shot Examples", system_prompt)
        self.assertIn("## User Question", user_prompt)
        self.assertIn("Who's the CEO of Agora?", user_prompt)

    def test_rag_answer_prompt_v2_is_sectioned_and_preserves_exact_insufficient_reply(self) -> None:
        insufficient_reply = "I couldn't find enough information in the available support knowledge base to answer that question."
        system_prompt = build_rag_answer_system_prompt(insufficient_reply=insufficient_reply)
        user_prompt = build_rag_answer_user_prompt(
            question="How do I join a channel?",
            context_block="chunk-1: Join a channel with the SDK's join method.",
            insufficient_reply=insufficient_reply,
            repair_mode=True,
        )

        self.assertIn("## Role", system_prompt)
        self.assertIn("only on the provided context chunks", system_prompt)
        self.assertIn("## User Question", user_prompt)
        self.assertIn("## Context Chunks", user_prompt)
        self.assertIn("## Output Requirements", user_prompt)
        self.assertIn("## Fallback Policy", user_prompt)
        self.assertIn("## Few-shot Examples", user_prompt)
        self.assertIn(insufficient_reply, user_prompt)
        self.assertIn("## Repair Requirements", user_prompt)

    def test_rag_sufficiency_prompt_v2_is_sectioned_and_conservative(self) -> None:
        system_prompt = build_rag_sufficiency_system_prompt()
        user_prompt = build_rag_sufficiency_user_prompt(
            message="How do I join a channel?",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join a channel?"}],
            route_summary={"scope_label": "agora_technical", "execution_action": "rag"},
            rag_answer="Use the SDK's join-channel method with the required parameters.",
            sources=["https://docs.agora.io/en/video-calling/get-started/get-started-sdk"],
            citations=[{"chunk_id": "chunk-1", "source_path": "official/join.md"}],
            evidence_summary={"quality_signals": {"selected_doc_count": 1}},
        )

        self.assertIn("## Role", system_prompt)
        self.assertIn("When in doubt, choose investigate", system_prompt)
        self.assertIn("Do not rewrite the answer.", system_prompt)
        self.assertIn("## Few-shot Examples", system_prompt)
        self.assertIn("## Customer Message", user_prompt)
        self.assertIn("## Candidate Answer", user_prompt)
        self.assertIn("## Evidence Summary", user_prompt)
        self.assertIn("## Required Output Schema", user_prompt)


if __name__ == "__main__":
    unittest.main()

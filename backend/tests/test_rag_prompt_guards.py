from __future__ import annotations

import unittest

from backend.services.rag_qa import _build_answer_prompt_for_mode
from backend.services.rag_sufficiency_prompt import build_rag_sufficiency_system_prompt


class RagPromptGuardTests(unittest.TestCase):
    def test_answer_prompt_keeps_generic_join_channel_questions_platform_agnostic(self) -> None:
        prompt = _build_answer_prompt_for_mode(
            "how to join channel",
            "chunk-1: Join a channel with joinChannel.",
            repair_mode=False,
        )

        self.assertIn(
            "If the user does not specify a platform or SDK, keep the answer at a safe cross-platform level.",
            prompt,
        )
        self.assertIn(
            "Do not include platform-specific callback names or one-SDK-only details unless the question asks for that platform.",
            prompt,
        )

    def test_sufficiency_prompt_allows_safe_generic_overviews_without_platform_details(self) -> None:
        prompt = build_rag_sufficiency_system_prompt()

        self.assertIn(
            "Do not require platform/version/configuration details when the customer's question is a generic how-to or overview request",
            prompt,
        )
        self.assertIn(
            'Use decision="answer" when a cited, high-level overview safely addresses the core question even if exact SDK-specific APIs vary by platform.',
            prompt,
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.llm_factory import LlmTextResult
from backend.services.llm_profiles import ModelProfile, OPENAI_RESPONSES_API
from backend.services.rag_context_budget import build_context_budget, build_packed_evidence
from backend.services.rag_qa import RetrievedChunk


class RagContextBudgetTests(unittest.TestCase):
    def test_build_context_budget_subtracts_reserved_components(self) -> None:
        budget = build_context_budget(
            context_window=4000,
            system_prompt_text="system " * 120,
            history_text="history " * 80,
            user_prompt_text="question " * 40,
            tool_schema_text="tools " * 10,
            reserved_output_tokens=600,
            buffer_tokens=300,
        )

        self.assertEqual(
            budget.available_context_tokens,
            budget.context_window
            - (
                budget.system_prompt_tokens
                + budget.history_tokens
                + budget.prompt_tokens
                + budget.tool_tokens
                + budget.reserved_output_tokens
                + budget.buffer_tokens
            ),
        )
        self.assertGreater(budget.available_context_tokens, 0)

    def test_build_packed_evidence_triggers_compression_when_raw_context_exceeds_budget(self) -> None:
        chunk_one = RetrievedChunk(
            chunk_id="chunk-1",
            text=(
                "Use joinChannel with the same channel name to enter the same communication session. "
                "The first user creates the channel and the last user leaving closes it. "
            )
            * 12,
            source_path="official/channel.md",
            similarity=0.97,
            h1="Channel",
            h2="Join a channel",
        )
        chunk_two = RetrievedChunk(
            chunk_id="chunk-2",
            text=(
                "A channel groups users who connect with the same channel name. "
                "Users in the same channel can communicate with each other in real time. "
            )
            * 10,
            source_path="official/channel-lifecycle.md",
            similarity=0.95,
            h1="Channel lifecycle",
            h2="How channels work",
        )
        compression_profile = ModelProfile(
            scenario="rag_context_compression",
            provider="openai",
            model="gpt-5.4-mini",
            api_mode=OPENAI_RESPONSES_API,
            api_key="test-key",
            reasoning_effort="low",
            temperature=0.0,
            timeout_seconds=8.0,
            max_retries=1,
        )

        with patch(
            "backend.services.rag_context_budget.invoke_responses_text",
            return_value=LlmTextResult(
                text=(
                    '{"evidence":[{"chunk_id":"chunk-1","packed_text":"Use joinChannel with the same channel name."},'
                    '{"chunk_id":"chunk-2","packed_text":"A channel groups users under one channel name."}]}'
                ),
                model_name="gpt-5.4-mini",
            ),
        ):
            packed = build_packed_evidence(
                question="How do I join a channel?",
                chunks=[chunk_one, chunk_two],
                system_prompt_text="system " * 20,
                user_prompt_text="question " * 12,
                tool_schema_text="",
                context_window=700,
                reserved_output_tokens=80,
                buffer_tokens=40,
                compression_enabled=True,
                compression_profile=compression_profile,
            )

        self.assertTrue(packed.compression_triggered)
        self.assertEqual(packed.compression_mode, "compressive")
        self.assertEqual(packed.compression_trigger_reason, "token_budget")
        self.assertLess(packed.packed_context_token_estimate, packed.raw_context_token_estimate)
        self.assertEqual(packed.extractive_segment_count, 2)
        self.assertEqual(packed.packed_evidence_count, 2)
        self.assertEqual(packed.chunk_ids, ["chunk-1", "chunk-2"])
        self.assertIn("[chunk-1]", packed.prompt_context)
        self.assertIn("Use joinChannel with the same channel name.", packed.prompt_context)
        self.assertEqual(packed.compression_model, "gpt-5.4-mini")

    def test_build_packed_evidence_keeps_raw_context_when_budget_is_sufficient(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="chunk-1",
            text="Use joinChannel with the same channel name.",
            source_path="official/channel.md",
            similarity=0.96,
            h1="Channel",
            h2="Join a channel",
        )

        packed = build_packed_evidence(
            question="How do I join a channel?",
            chunks=[chunk],
            system_prompt_text="system",
            user_prompt_text="question",
            tool_schema_text="",
            context_window=4000,
            reserved_output_tokens=200,
            buffer_tokens=200,
            compression_enabled=True,
            compression_profile=None,
        )

        self.assertFalse(packed.compression_triggered)
        self.assertEqual(packed.compression_mode, "raw")
        self.assertEqual(packed.chunk_ids, ["chunk-1"])
        self.assertGreaterEqual(packed.packed_context_token_estimate, 1)


if __name__ == "__main__":
    unittest.main()
